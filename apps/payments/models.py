"""UPI payment record model."""

from django.db import models
from django.conf import settings


class PaymentRecord(models.Model):
    """Tracks UPI and manual payment receipts."""

    class PaymentMethod(models.TextChoices):
        UPI_INTENT = 'UPI_INTENT', 'UPI Intent'
        MANUAL = 'MANUAL', 'Manual Upload'

    class PaymentStatus(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        # Phase 1 MVR flow: resident self-declares (UPI intent or manual receipt),
        # an RWA admin approves it. Only PENDING_* rows are awaiting verification.
        PENDING_VERIFICATION = 'PENDING_VERIFICATION', 'Pending Verification'
        SUCCESS = 'SUCCESS', 'Success'
        FAILED = 'FAILED', 'Failed'
        REJECTED = 'REJECTED', 'Rejected'
        REFUNDED = 'REFUNDED', 'Refunded'

    invoice = models.ForeignKey(
        'invoices.Invoice', on_delete=models.CASCADE, related_name='payments'
    )
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    method = models.CharField(max_length=20, choices=PaymentMethod)
    status = models.CharField(max_length=20, choices=PaymentStatus)
    utr_number = models.CharField(max_length=16, null=True, blank=True)
    payment_app = models.CharField(max_length=50, null=True)
    receipt_image = models.ImageField(upload_to='receipts/', null=True, blank=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='verified_payments'
    )
    verified_at = models.DateTimeField(null=True)
    # Set by the RWA admin when rejecting a self-declared payment (MVR-004).
    verification_note = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'payment_record'
        indexes = [
            models.Index(fields=['invoice', 'status']),
            models.Index(fields=['utr_number']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        return f"{self.invoice_id} – {self.status} (₹{self.amount})"  # type: ignore[str-bytes-safe]
