"""Invoice generation and overdue handling (doc 04 §10.3)."""

import logging
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.db import transaction

from apps.users.models import CustomUser

from .models import Invoice

logger = logging.getLogger(__name__)

UNPAID_STATUSES = [Invoice.InvoiceStatus.GENERATED, Invoice.InvoiceStatus.PARTIAL]


def current_period(today=None):
    """Billing period key for ``today`` in ``YYYY-MM`` form."""
    today = today or date.today()
    return f'{today.year}-{today.month:02d}'


def _resident_for(unit):
    """The account most likely liable for this unit's maintenance this month."""
    phone = unit.tenant_phone if unit.is_rented and unit.tenant_phone else unit.owner_phone
    if not phone:
        return None
    return CustomUser.objects.filter(phone=phone, society=unit.society).first()


def generate_invoices_for_society(society, period=None, actor=None):
    """Issue flat-rate maintenance invoices for every live unit (INV-001).

    Idempotent: re-running for a period skips units that already have an
    invoice, which is what makes the monthly cron safe to retry after a crash.

    Phase 1 rules:
      - flat base amount (``MAINTENANCE_FLAT_RATE``) per unit
      - +10% non-occupancy surcharge on ``base_maintenance`` when the flat is
        rented (NONOC-001…006)
      - late fee and GST stay 0 (Phase 2)
    """
    period = period or current_period()
    base_amount = settings.MAINTENANCE_FLAT_RATE
    # Due date belongs to the billing period, not to the day the job runs, so a
    # backfill (or a retry after a crash) cannot hand an old bill a new deadline.
    year, month = (int(part) for part in period.split('-'))
    due_date = date(year, month, settings.INVOICE_DUE_DAY)

    created = 0
    skipped = 0

    for unit in society.units.filter(is_deleted=False).order_by('building', 'floor', 'unit_number'):
        if Invoice.objects.filter(society=society, unit=unit, billing_period=period).exists():
            skipped += 1
            continue

        surcharge = Decimal('0')
        if unit.is_rented:
            surcharge = (unit.base_maintenance * settings.NON_OCCUPANCY_SURCHARGE_RATE).quantize(Decimal('0.01'))

        # Building is part of the number: two buildings can share a unit number
        # (A-101 and B-101), and invoice numbers are unique across the platform.
        invoice_number = f'INV-{period}-{society.id:04d}-{unit.building}{unit.unit_number}'

        Invoice.objects.create(
            society=society,
            unit=unit,
            resident=_resident_for(unit),
            invoice_number=invoice_number,
            billing_period=period,
            base_amount=base_amount,
            non_occupancy_surcharge=surcharge,
            total_amount=base_amount + surcharge,
            status=Invoice.InvoiceStatus.GENERATED,
            due_date=due_date,
        )
        created += 1

    logger.info(
        'Invoice run %s for %s: created=%s skipped=%s actor=%s',
        period, society.subdomain, created, skipped, getattr(actor, 'phone', None) or actor or 'cron',
    )
    return {'period': period, 'created': created, 'skipped': skipped}


def mark_overdue_invoices(as_of=None):
    """Flip unpaid invoices past their due date to OVERDUE (INV-004).

    Run from the same monthly command; no late-fee calculation yet (Phase 2).
    """
    as_of = as_of or date.today()
    overdue = Invoice.objects.filter(
        status__in=UNPAID_STATUSES, due_date__lt=as_of
    ).update(status=Invoice.InvoiceStatus.OVERDUE)
    if overdue:
        logger.info('Marked %s invoice(s) OVERDUE as of %s', overdue, as_of)
    return overdue


def apply_payment_to_invoice(invoice, amount, *, method=None, utr_number=None, paid_on=None):
    """Record money received against an invoice and move its status.

    Called when an RWA admin approves a self-declared payment (MVR-003).
    """
    amount = Decimal(str(amount))
    with transaction.atomic():
        invoice.refresh_from_db()
        invoice.paid_amount = (invoice.paid_amount or Decimal('0')) + amount
        invoice.payment_method = method or invoice.payment_method
        invoice.utr_number = utr_number or invoice.utr_number
        invoice.paid_date = paid_on or date.today()

        if invoice.paid_amount >= invoice.total_amount:
            invoice.status = Invoice.InvoiceStatus.PAID
        elif invoice.paid_amount > 0:
            invoice.status = Invoice.InvoiceStatus.PARTIAL
        invoice.save(update_fields=['paid_amount', 'payment_method', 'utr_number', 'paid_date', 'status'])
    return invoice
