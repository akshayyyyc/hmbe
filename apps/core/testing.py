"""Shared test fixtures for the API tests.

``api_client`` authenticates with a real JWT rather than
``APIClient.force_authenticate``: the tenant is stamped by
``apps.core.authentication.TenantJWTAuthentication``, so bypassing the
authentication classes would test a request shape the product can never
produce (and would hide tenant bugs like the one in doc 04 §2.3).
"""

from datetime import date, timedelta
from decimal import Decimal

from rest_framework.test import APIClient

from apps.society.models import Society, SocietySubscription
from apps.units.models import Unit
from apps.users.models import CustomUser
from apps.users.tokens import issue_tokens

PASSWORD = 'TestPassword@123'


def make_society(subdomain='sunrise', name='Sunrise Residency', **overrides):
    defaults = {
        'name': name,
        'subdomain': subdomain,
        'address': '123 Main Street',
        'pincode': '560038',
        'city': 'Bangalore',
        'state': 'Karnataka',
        'total_units': 10,
        'president_name': 'Anita Desai',
        'president_phone': '+91-9800000001',
    }
    defaults.update(overrides)
    society = Society.objects.create(**defaults)
    society.activate_trial()
    SocietySubscription.objects.create(
        society=society,
        tier=society.subscription_tier,
        base_amount=Decimal('1999.00'),
        status=SocietySubscription.PaymentStatus.PENDING,
        invoice_date=date.today(),
        due_date=society.trial_end_date,
    )
    return society


def make_user(society=None, phone='+91-9800000100', role=CustomUser.UserRole.RESIDENT,
              first='Test', last='Resident', flat='101', **overrides):
    defaults = {
        'username': phone,
        'phone': phone,
        'first_name': first,
        'last_name': last,
        'email': f'{phone.replace("+", "").replace("-", "")}@example.com',
        'role': role,
        'society': society,
        'flat_number': flat,
        'resident_status': CustomUser.ResidentStatus.VERIFIED,
        'is_phone_verified': True,
    }
    defaults.update(overrides)
    user = CustomUser.objects.create(**defaults)
    user.set_password(PASSWORD)
    user.save(update_fields=['password'])
    return user


def make_unit(society, building='A', unit_number='101', floor=1, **overrides):
    defaults = {
        'society': society,
        'building': building,
        'floor': floor,
        'unit_number': unit_number,
        'unit_type': '2BHK',
        'base_maintenance': Decimal('2000.00'),
        'owner_name': 'Owner One',
        'owner_phone': '+91-9800000201',
    }
    defaults.update(overrides)
    return Unit.objects.create(**defaults)


def committee(society, tag='01'):
    """A president/treasurer pair plus one plain resident, on numbers unique platform-wide.

    Phone is the identity key, so two societies in one test need disjoint
    numbers; ``tag`` shifts the block (e.g. ``'01'`` and ``'02'``).
    """
    president = make_user(society, f'+91-9800000{tag}1', CustomUser.UserRole.RWA_PRESIDENT, first='Anita')
    treasurer = make_user(society, f'+91-9800000{tag}3', CustomUser.UserRole.RWA_TREASURER, first='Meera', flat='201')
    resident = make_user(society, f'+91-9800000{tag}0', CustomUser.UserRole.RESIDENT, first='Suresh', flat='202')
    return president, treasurer, resident


def api_client(user):
    """APIClient carrying a freshly minted access token for ``user``."""
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {issue_tokens(user)['access']}")
    client.user = user
    return client


def otp_for(phone, purpose='LOGIN'):
    """Create a real OTP record and return its plaintext (delivery is a no-op in DEBUG)."""
    from apps.users.utils import create_otp

    _record, code = create_otp(phone=phone, purpose=purpose)
    return code


def login_payload(user, purpose='LOGIN'):
    """Body for ``POST /api/v1/auth/verify-otp/`` that actually validates."""
    return {'phone': user.phone, 'otp': otp_for(user.phone, purpose), 'purpose': purpose}


def days_from_now(days):
    return date.today() + timedelta(days=days)
