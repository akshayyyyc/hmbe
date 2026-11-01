"""Unit registry and vehicle tracking models."""

from django.db import models
from django.conf import settings


class Unit(models.Model):
    """A residential unit (flat) within a society."""

    class UnitType(models.TextChoices):
        STUDIO = 'STUDIO', 'Studio'
        RK = 'RK', 'RK'
        BHK_1 = '1BHK', '1 BHK'
        BHK_2 = '2BHK', '2 BHK'
        BHK_3 = '3BHK', '3 BHK'
        DUPLEX = 'DUPLEX', 'Duplex'
        PENTHOUSE = 'PENTHOUSE', 'Penthouse'

    class OccupancyType(models.TextChoices):
        OWNER_OCCUPIED = 'OWNER_OCCUPIED', 'Owner Occupied'
        RENTED = 'RENTED', 'Rented'
        VACANT = 'VACANT', 'Vacant'

    society = models.ForeignKey(
        'society.Society', on_delete=models.CASCADE, related_name='units'
    )
    building = models.CharField(max_length=10, db_index=True)  # e.g. A, B, C
    floor = models.IntegerField()
    unit_number = models.CharField(max_length=10, db_index=True)
    unit_type = models.CharField(max_length=20, choices=UnitType)
    area_sqft = models.DecimalField(max_digits=8, decimal_places=2, null=True)

    # ── Occupancy ─────────────────────────────────────────────────────
    occupancy_type = models.CharField(
        max_length=20, choices=OccupancyType, default=OccupancyType.OWNER_OCCUPIED
    )
    is_rented = models.BooleanField(default=False)

    # ── Billing defaults ──────────────────────────────────────────────
    base_maintenance = models.DecimalField(
        max_digits=10, decimal_places=2, default=2000.00
    )

    # ── Owner / tenant contacts ───────────────────────────────────────
    owner_name = models.CharField(max_length=255)
    owner_phone = models.CharField(max_length=15)
    owner_email = models.EmailField(null=True, blank=True)

    tenant_name = models.CharField(max_length=255, null=True, blank=True)
    tenant_phone = models.CharField(max_length=15, null=True, blank=True)
    tenant_email = models.EmailField(null=True, blank=True)

    parking_number = models.CharField(max_length=20, null=True, blank=True)

    # Soft delete (doc 04 §6.4): invoices and payment history must survive.
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'unit'
        # Uniqueness applies only to live units, so a soft-deleted unit does not
        # block re-registering the same flat later.
        constraints = [
            models.UniqueConstraint(
                fields=['society', 'building', 'unit_number'],
                name='unique_active_unit',
                # Conditional: only live units collide, which is what lets a
                # soft-deleted flat be re-registered (doc 04 §6.4).
                condition=~models.Q(is_deleted=True),
            )
        ]
        indexes = [
            models.Index(fields=['society', 'building', 'floor']),
            models.Index(fields=['society', 'occupancy_type']),
            models.Index(fields=['society', 'is_deleted']),
        ]

    def __str__(self):
        return f"{self.society.name} – {self.building}-{self.floor}-{self.unit_number}"

    @property
    def full_unit_id(self):
        """Return identifier like ``BLD-FLOOR-101``."""
        return f"{self.building}-{self.floor}-{self.unit_number}"

    def soft_delete(self):
        """Mark the unit deleted without destroying billing history."""
        from django.utils import timezone

        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(update_fields=['is_deleted', 'deleted_at', 'updated_at'])


class Vehicle(models.Model):
    """Vehicle registered to a unit."""

    class VehicleType(models.TextChoices):
        TWO_WHEELER = 'TWO_WHEELER', 'Two Wheeler'
        FOUR_WHEELER = 'FOUR_WHEELER', 'Four Wheeler'
        THREE_WHEELER = 'THREE_WHEELER', 'Three Wheeler'

    unit = models.ForeignKey(Unit, on_delete=models.CASCADE, related_name='vehicles')
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    vehicle_type = models.CharField(max_length=20, choices=VehicleType)
    vehicle_number = models.CharField(max_length=20, db_index=True)
    color = models.CharField(max_length=50, null=True, blank=True)
    is_primary = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'vehicle'

    def __str__(self):
        return f"{self.vehicle_number} ({self.unit.full_unit_id})"
