"""UPI intent construction (doc 04 §9.1, doc 07).

Phase 1 has no gateway: the backend hands the client a ``upi://pay`` URI that
launches the resident's UPI app pre-filled with the amount and the invoice
number as the transaction note. Nothing here verifies the transfer — that is
the manual MVR flow in ``views.verify_payment``.
"""

from decimal import Decimal
from urllib.parse import quote

# UPI spec caps the transaction note; invoice numbers fit comfortably, but the
# clamp keeps a hand-edited note from producing a link the app will reject.
MAX_NOTE_LENGTH = 50


def build_upi_link(*, amount, transaction_note, payee_vpa, payee_name):
    """Return the deep link for a fixed-amount INR collect request."""
    amount = Decimal(str(amount)).quantize(Decimal('0.01'))
    params = [
        ('pa', payee_vpa),
        ('pn', payee_name),
        ('am', f'{amount:.2f}'),
        ('cu', 'INR'),
        ('tn', (transaction_note or '')[:MAX_NOTE_LENGTH]),
    ]
    query = '&'.join(f'{key}={quote(str(value))}' for key, value in params)
    return f'upi://pay?{query}'
