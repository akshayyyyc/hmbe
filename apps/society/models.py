"""Models for the Society and Subscription domain."""

from django.db import models
from django.conf import settings


class Society(models.Model):
    """Represents a registered housing society / residential complex.

    Phase 1: Starter tier only (₹1,999/month). Full multi-tier version is
    canonical in ``planning/PRD/09-superadmin-subscription-pricing.md`` §2.3.
    """

    class SubscriptionTier(models.TextChoices):
        STARTER = 'STARTER', 'Starter (₹1,999/month)'

    class OnboardingStatus(models.TextChoices):
        PENDING = 'PENDING', 'Pending Verification'
        TRIAL = 'TRIAL', 'Trial Period'
        ACTIVE = 'ACTIVE', 'Active'
        SUSPENDED = 'SUSPENDED', 'Suspended'

    # ── Identity ───────────────────────────────────────────────────────
    name = models.CharField(max_length=255, db_index=True)
    subdomain = models.CharField(max_length=100, unique=True, db_index=True)
    address = models.TextField()
    pincode = models.CharField(max_length=10, db_index=True)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100)

    # ── Configuration ─────────────────────────────────────────────────
    total_units = models.IntegerField()
    registered_units = models.IntegerField(default=0)

    # ── Contact ────────────────────────────────────────────────────────
    president_name = models.CharField(max_length=255, null=True)
    president_phone = models.CharField(max_length=15, null=True)
    president_email = models.EmailField(null=True)

    # ── Subscription (Phase 1: Starter only) ──────────────────────────
    subscription_tier = models.CharField(
        max_length=20, choices=SubscriptionTier, default=SubscriptionTier.STARTER
    )
    subscription_status = models.CharField(
        max_length=20, choices=OnboardingStatus, default=OnboardingStatus.PENDING
    )

    # ── Trial tracking ────────────────────────────────────────────────
    trial_start_date = models.DateField(null=True)
    trial_end_date = models.DateField(null=True)

    # ── Metadata ──────────────────────────────────────────────────────
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'society'
        indexes = [
            models.Index(fields=['subdomain']),
            models.Index(fields=['pincode']),
        ]

    def __str__(self):
        return f"{self.name} ({self.subdomain})"

    def activate_trial(self):
        """Activate a 30-day free trial."""
        from datetime import date, timedelta

        today = date.today()
        self.subscription_status = self.OnboardingStatus.TRIAL
        self.trial_start_date = today
        self.trial_end_date = today + timedelta(days=30)
        self.save()

    def is_trial_active(self):
        """Return True if the trial period is still running."""
        if not self.trial_end_date:
            return False
        from datetime import date

        return self.trial_end_date >= date.today()

    def days_until_trial_end(self):
        """Days remaining in trial, or ``None`` if trial hasn't started."""
        if not self.trial_end_date:
            return None
        from datetime import date

        return (self.trial_end_date - date.today()).days


class SocietySubscription(models.Model):
    """Billing / subscription record for a society.

    Phase 1: Flat ₹1,999/month only. Full multi-tier version is in the PRD.
    The Society model embeds ``subscription_tier`` and ``subscription_status``
    as convenience fields; this model is the authoritative financial record.
    """

    class BillingCycle(models.TextChoices):
        MONTHLY = 'MONTHLY', 'Monthly'

    class PaymentStatus(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        PAID = 'PAID', 'Paid'
        FAILED = 'FAILED', 'Failed'

    society = models.OneToOneField(
        Society, on_delete=models.CASCADE, related_name='subscription'
    )
    tier = models.CharField(max_length=20, choices=Society.SubscriptionTier)
    billing_cycle = models.CharField(
        max_length=10, choices=BillingCycle, default=BillingCycle.MONTHLY
    )
    base_amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(
        max_length=20, choices=PaymentStatus, default=PaymentStatus.PENDING
    )
    invoice_date = models.DateField(null=True)
    due_date = models.DateField(null=True)
    paid_date = models.DateField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'society_subscription'

    def save(self, *args, **kwargs):
        if not self.base_amount:
            self.base_amount = 1999.00
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.society.name} – {self.tier} (₹{self.base_amount})"
