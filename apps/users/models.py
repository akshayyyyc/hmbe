"""User authentication and OTP verification models."""

from django.contrib.auth.models import AbstractUser
from django.db import models


class CustomUser(AbstractUser):
    """Extended user model with role-based access control.

    Uses phone as the primary identifier; email is optional.
    """

    class UserRole(models.TextChoices):
        RESIDENT = 'RESIDENT', 'Resident / Owner'
        LANDLORD = 'LANDLORD', 'Landlord / Investor'
        RWA_PRESIDENT = 'RWA_PRESIDENT', 'RWA President'
        RWA_SECRETARY = 'RWA_SECRETARY', 'RWA Secretary'
        RWA_TREASURER = 'RWA_TREASURER', 'RWA Treasurer'
        SECURITY_GUARD = 'SECURITY_GUARD', 'Security Guard'
        STAFF = 'STAFF', 'Staff'
        PLATFORM_ADMIN = 'PLATFORM_ADMIN', 'Platform Admin'

    class ResidentStatus(models.TextChoices):
        UNASSIGNED = 'UNASSIGNED', 'Unassigned (not yet verified)'
        VERIFIED = 'VERIFIED', 'Verified Resident'
        PENDING = 'PENDING', 'Pending Verification'

    # Override email – allow null because phone is the primary auth field.
    # NOTE: PostgreSQL allows multiple NULLs in unique columns; Django ORM
    # won't enforce uniqueness for NULL values. Consider a partial unique
    # index at the DB level if strict uniqueness is required later.
    email = models.EmailField(unique=True, null=True, blank=True)
    phone = models.CharField(max_length=15, unique=True, db_index=True)

    role = models.CharField(max_length=30, choices=UserRole)
    society = models.ForeignKey(
        'society.Society',
        on_delete=models.CASCADE,
        null=True,
        related_name='users',
    )

    resident_status = models.CharField(
        max_length=20, choices=ResidentStatus, default=ResidentStatus.UNASSIGNED
    )
    flat_number = models.CharField(max_length=20, null=True)
    is_phone_verified = models.BooleanField(default=False)
    is_email_verified = models.BooleanField(default=False)

    profile_photo = models.ImageField(upload_to='profiles/', null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_login_at = models.DateTimeField(null=True)

    class Meta:
        db_table = 'custom_user'
        indexes = [
            models.Index(fields=['phone']),
            models.Index(fields=['society', 'role']),
        ]

    def __str__(self):
        name = self.get_full_name() or self.username
        return f"{name} ({self.phone})"

    @property
    def is_rwa_admin(self):
        """Return True for RWA administrative roles."""
        return self.role in {
            self.UserRole.RWA_PRESIDENT,
            self.UserRole.RWA_SECRETARY,
            self.UserRole.RWA_TREASURER,
        }

    @property
    def is_resident_or_landlord(self):
        """Return True for resident / landlord roles."""
        return self.role in {self.UserRole.RESIDENT, self.UserRole.LANDLORD}


class OTPVerification(models.Model):
    """OTP verification records for phone or email verification."""

    class Purpose(models.TextChoices):
        REGISTRATION = 'REGISTRATION', 'User Registration'
        LOGIN = 'LOGIN', 'Login Verification'
        PASSWORD_RESET = 'PASSWORD_RESET', 'Password Reset'
        PHONE_VERIFY = 'PHONE_VERIFY', 'Phone Verification'
        EMAIL_VERIFY = 'EMAIL_VERIFY', 'Email Verification'

    phone = models.CharField(max_length=15, null=True, blank=True)
    email = models.EmailField(null=True, blank=True)
    # SHA-256 hex digest (64 chars) of the 6-digit code — the plaintext OTP is
    # never persisted (doc 04 §3.1).
    otp = models.CharField(max_length=128)
    purpose = models.CharField(max_length=20, choices=Purpose)
    is_verified = models.BooleanField(default=False)
    attempt_count = models.IntegerField(default=0)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'otp_verification'
        indexes = [
            models.Index(fields=['phone', 'purpose']),
            models.Index(fields=['email', 'purpose']),
            models.Index(fields=['expires_at']),
        ]

    def __str__(self):
        target = self.phone or self.email or 'unknown'
        return f"OTP for {target} – {self.purpose}"

    def is_expired(self):
        """Return True if the OTP has passed its expiry time."""
        from datetime import datetime

        return datetime.now() > self.expires_at

    def increment_attempts(self, max_attempts=3):
        """Increment attempt counter; unverify if max exceeded."""
        self.attempt_count += 1
        if self.attempt_count >= max_attempts:
            self.is_verified = False
        self.save()
