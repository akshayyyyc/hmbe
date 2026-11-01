"""Unit registry services: counters and CSV bulk import (doc 04 §6.5)."""

import csv
import io
import logging

from django.db import transaction

from .models import Unit

logger = logging.getLogger(__name__)

# Header row accepted from the import template. Optional columns may be blank.
# ``floor`` is part of the registry template in doc 04 §6.5 and the column the
# billing engine needs, so a spreadsheet without it is rejected up front instead
# of failing row-by-row on a serializer error.
REQUIRED_COLUMNS = ['building', 'floor', 'unit_number', 'unit_type', 'owner_name', 'owner_phone']
MAX_IMPORT_ROWS = 5000


class ImportError_(Exception):  # noqa: A001 - intentional distinct name
    """Raised when the uploaded file itself cannot be read."""


def refresh_registered_units(society):
    """Recompute ``registered_units`` from live units.

    The denormalised counter exists so the treasurer dashboard and society
    profile do not count rows on every read; keeping it in one place stops the
    CRUD views and the importer from drifting apart.
    """
    count = society.units.filter(is_deleted=False).count()
    if society.registered_units != count:
        society.registered_units = count
        society.save(update_fields=['registered_units', 'updated_at'])
    return count


def parse_upload(uploaded):
    """Decode an uploaded CSV into ``(rows, header_ok)``."""
    name = getattr(uploaded, 'name', '') or ''
    if not name.lower().endswith('.csv'):
        raise ImportError_('Upload a .csv file exported from the unit registry template')

    raw = uploaded.read()
    if len(raw) > 2 * 1024 * 1024:
        raise ImportError_('File is too large (limit 2 MB, about 5,000 units)')

    text = raw.decode('utf-8-sig', errors='replace')
    reader = csv.DictReader(io.StringIO(text))
    header = [h.strip().lower() for h in (reader.fieldnames or [])]
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        raise ImportError_(f'Missing required column(s): {", ".join(missing)}')

    rows = []
    for index, row in enumerate(reader, start=2):  # header is line 1
        if not any((v or '').strip() for v in row.values()):
            continue
        rows.append((index, {k.strip().lower(): (v or '').strip() for k, v in row.items() if k}))
        if len(rows) > MAX_IMPORT_ROWS:
            raise ImportError_(f'More than {MAX_IMPORT_ROWS} rows in one upload')
    return rows


def bulk_import_units(society, rows, serializer_class):
    """Validate and insert CSV rows, reporting failures per row.

    Row-level failures never abort the import — the society gets what is valid
    and a precise list of what to fix, which is the behaviour RWA volunteers
    expect from a spreadsheet workflow.
    """
    imported = 0
    errors = []

    for line, row in rows:
        try:
            with transaction.atomic():
                serializer = serializer_class(
                    data=row,
                    context={'request': None, 'society': society},
                )
                if not serializer.is_valid():
                    # Name the field, otherwise "This field is required." sends a
                    # volunteer hunting through a 200-row spreadsheet.
                    message = '; '.join(
                        f'{field}: {(msgs[0] if isinstance(msgs, list) and msgs else msgs)}'
                        for field, msgs in serializer.errors.items()
                    )
                    raise ValueError(message)
                serializer.save(society=society)
                imported += 1
        except ValueError as exc:
            errors.append(
                {
                    'row': line,
                    'building': row.get('building', ''),
                    'unit_number': row.get('unit_number', ''),
                    'error': str(exc),
                }
            )

    refresh_registered_units(society)
    return {'imported': imported, 'failed': len(errors), 'errors': errors}
