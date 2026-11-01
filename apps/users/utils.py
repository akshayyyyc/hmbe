"""Phone normalisation, OTP generation/hashing and delivery placeholders (doc 04 §16)."""

import hashlib
import logging
import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import OTPVerification

logger = logging.getLogger(__name__)

# Purposes accepted from unauthenticated callers. PASSWORD_RESET is intentionally
# absent until the reset flow exists (Phase 2).
PUBLIC_PURPOSES = {'LOGIN', 'REGISTRATION', 'PHONE_VERIFY'}


def normalize_phone(raw):
    """Return an Indian mobile number as ``+91-XXXXXXXXXX``, or ``None``.

    Phone is the product's primary identity key, so every write path funnels
    through here — otherwise ``9876543210``, ``+91 98765 43210`` and
    ``098765-43210`` would each become a different resident.
    """
    if not raw:
        return None

    digits = re.sub(r'\D', '', str(raw))
    if len(digits) == 12 and digits.startswith('91'):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith('0'):
        digits = digits[1:]

    if not re.fullmatch(r'[6-9]\d{9}', digits):
        return None
    return f'+91-{digits}'


def hash_otp(code):
    """Hex SHA-256 of the code. Plaintext OTPs are never stored."""
    return hashlib.sha256(str(code).encode()).hexdigest()


def generate_otp():
    """Cryptographically random 6-digit code (OTP codes are attacker-guessable)."""
    return f"{secrets.randbelow(900_000) + 100_000}"


def create_otp(*, purpose, phone=None, email=None, expiry_seconds=None):
    """Persist a hashed OTP and return ``(record, plaintext_code)``.

    Earlier codes for the same target/purpose are not deleted: ``pending_otp_for``
    always takes the **newest** unverified, unexpired row, so an old code can never
    be the one that validates. Leaving the history in place is what keeps the
    resend cooldown and the attempt counters auditable.
    """
    code = generate_otp()
    record = OTPVerification.objects.create(
        phone=phone,
        email=email,
        otp=hash_otp(code),
        purpose=purpose,
        is_verified=False,
        expires_at=timezone.now() + timedelta(seconds=expiry_seconds or settings.OTP_EXPIRY_SECONDS),
    )
    return record, code


def deliver_otp(phone, code):
    """Hand the code to the SMS provider.

    Phase 1 has no SMS provider contract yet, so in DEBUG the code is logged for
    local testing. In production the code is never logged — the delivery itself
    is a no-op until Gupshup/Twilio is wired (Phase 2). Flagged as a launch
    dependency: without it, non-dev residents cannot complete OTP login.
    """
    if settings.DEBUG:
        logger.info('[DEV] OTP for %s: %s', phone, code)
        return
    logger.warning('SMS provider not configured — OTP for %s was not delivered', phone)


def pending_otp_for(phone, purpose):
    """Most recent unverified, unexpired OTP for a phone/purpose, if any."""
    return (
        OTPVerification.objects.filter(
            phone=phone,
            purpose=purpose,
            is_verified=False,
            expires_at__gt=timezone.now(),
        )
        .order_by('-created_at')
        .first()
    )


def consume_otp(*, phone, otp, purpose):
    """Validate an OTP and mark it used, enforcing the attempt ceiling.

    Returns ``(True, None)`` on success, otherwise ``(False, (code, message))``
    so callers can render the project error envelope.
    """
    record = pending_otp_for(phone, purpose)
    if not record:
        return False, ('INVALID_OTP', 'Invalid or expired OTP')

    if record.otp != hash_otp(otp):
        record.increment_attempts(max_attempts=settings.OTP_MAX_ATTEMPTS)
        if record.attempt_count >= settings.OTP_MAX_ATTEMPTS:
            return False, (
                'OTP_MAX_ATTEMPTS',
                'Maximum attempts exceeded. Please request a new OTP',
            )
        left = settings.OTP_MAX_ATTEMPTS - record.attempt_count
        return False, ('INVALID_OTP', f'Incorrect OTP. {left} attempt(s) left')

    record.is_verified = True
    record.save(update_fields=['is_verified'])
    return True, None


def seconds_until_resend_allows(phone):
    """Seconds remaining in the resend cooldown for this phone, or 0."""
    latest = (
        OTPVerification.objects.filter(phone=phone)
        .order_by('-created_at')
        .values_list('created_at', flat=True)
        .first()
    )
    if not latest:
        return 0
    elapsed = (timezone.now() - latest).total_seconds()
    remaining = settings.OTP_RESEND_COOLDOWN_SECONDS - elapsed
    return int(remaining) if remaining > 0 else 0
