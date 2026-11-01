"""Invoice / billing model."""

from django.db import models
from django.conf import settings


class Invoice(models.Model):
    """Maintenance invoice for a unit within a society."""

    class InvoiceStatus(models.TextChoices):
        DRAFT = 'DRAFT', 'Draft'
        GENERATED = 'GENERATED', 'Generated'
        PAID = 'PAID', 'Paid'
        OVERDUE = 'OVERDUE', 'Overdue'
        PARTIAL = 'PARTIAL', 'Partial'

    society = models.ForeignKey(
        'society.Society', on_delete=models.CASCADE, related_name='invoices'
    )
    unit = models.ForeignKey(
        'units.Unit', on_delete=models.CASCADE, related_name='invoices'
    )
    resident = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, related_name='invoices'
    )
    invoice_number = models.CharField(max_length=50, unique=True, db_index=True)
    billing_period = models.CharField(max_length=7)  # YYYY-MM

    base_amount = models.DecimalField(max_digits=10, decimal_places=2)
    water_charge = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    sinking_fund = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    amenity_charges = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    non_occupancy_surcharge = models.DecimalField(
        max_digits=10, decimal_places=2, default=0
    )
    late_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    gst_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)

    status = models.CharField(max_length=20, choices=InvoiceStatus)
    due_date = models.DateField()
    paid_date = models.DateField(null=True)
    paid_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    payment_method = models.CharField(max_length=50, null=True)
    utr_number = models.CharField(max_length=16, null=True)

    generated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'invoice'
        indexes = [
            models.Index(fields=['society', 'billing_period']),
            models.Index(fields=['unit', 'billing_period']),
            models.Index(fields=['status']),
            models.Index(fields=['invoice_number']),
        ]

    def __str__(self):
        return f"{self.invoice_number} – {self.unit.full_unit_id}"  # type: ignore[union-attr]

    @property
    def outstanding_amount(self):
        """Amount still owed on this invoice (kept as Decimal — never float)."""
        from decimal import Decimal

        return max(Decimal('0'), (self.total_amount or Decimal('0')) - (self.paid_amount or Decimal('0')))
