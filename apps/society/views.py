"""Society onboarding, society lookup and subscription endpoints (doc 04 §4, §11)."""

import logging
from datetime import date, timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated

from apps.core.pagination import paginated
from apps.core.permissions import HasSociety, IsPlatformAdmin, IsRWAAdmin
from apps.core.responses import error, ok, validation_error
from apps.payments.services import build_upi_link
from apps.users.models import CustomUser
from apps.users.serializers import UserSerializer
from apps.users.utils import consume_otp, create_otp, deliver_otp

from .models import Society, SocietySubscription
from .serializers import (
    SocietyDetailSerializer,
    SocietyPublicSerializer,
    SocietyRegisterSerializer,
    SocietySubscriptionSerializer,
)

logger = logging.getLogger(__name__)


# ── Onboarding (public) ───────────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([AllowAny])
def register_society(request):
    """Create a society, start its 30-day trial and seed the president account (§4.1)."""
    serializer = SocietyRegisterSerializer(data=request.data)
    if not serializer.is_valid():
        return validation_error(serializer.errors)

    data = serializer.validated_data
    president_phone = data['president_phone']

    # The president's phone is an identity key: it may not already belong to a
    # different tenant, and it must not be mid-registration on another society.
    existing = CustomUser.objects.filter(phone=president_phone).first()
    if existing and existing.society_id:
        return error(
            'PHONE_ALREADY_LINKED',
            "This phone number is already linked to another society's account",
            status_code=409,
        )

    first, *rest = (data.get('president_name') or '').split()
    with transaction.atomic():
        society = serializer.save()
        society.activate_trial()

        SocietySubscription.objects.create(
            society=society,
            tier=society.subscription_tier,
            billing_cycle=SocietySubscription.BillingCycle.MONTHLY,
            base_amount=settings.STARTER_MONTHLY_FEE,
            status=SocietySubscription.PaymentStatus.PENDING,
            invoice_date=date.today(),
            due_date=society.trial_end_date,
        )

        president, created_user = CustomUser.objects.get_or_create(
            phone=president_phone,
            defaults={
                'username': president_phone,
                'first_name': first,
                'last_name': ' '.join(rest),
                'email': data.get('president_email'),
                'role': CustomUser.UserRole.RWA_PRESIDENT,
                'society': society,
                'resident_status': CustomUser.ResidentStatus.VERIFIED,
            },
        )
        if not created_user and president.society_id is None:
            president.society = society
            president.role = CustomUser.UserRole.RWA_PRESIDENT
            president.resident_status = CustomUser.ResidentStatus.VERIFIED
            president.save(update_fields=['society', 'role', 'resident_status', 'updated_at'])

    _record, code = create_otp(phone=president_phone, purpose='REGISTRATION')
    deliver_otp(president_phone, code)

    logger.info('Society onboarded: %s (%s)', society.name, society.subdomain)
    return ok(
        {
            **SocietyPublicSerializer(society).data,
            'subscription_status': society.subscription_status,
            'trial_start_date': society.trial_start_date,
            'trial_end_date': society.trial_end_date,
            'president_phone': president_phone,
            'message': 'Society registered. 30-day free trial activated. Verify the OTP sent to the president phone.',
        },
        status_code=201,
    )


@api_view(['POST'])
@permission_classes([AllowAny])
def verify_society_otp(request):
    """Verify the onboarding OTP, then hand the president a JWT session (§4.1)."""
    from apps.users.serializers import VerifyOtpSerializer

    serializer = VerifyOtpSerializer(data={**request.data, 'purpose': 'REGISTRATION'})
    if not serializer.is_valid():
        return validation_error(serializer.errors)

    phone = serializer.validated_data['phone']
    valid, problem = consume_otp(phone=phone, otp=serializer.validated_data['otp'], purpose='REGISTRATION')
    if not valid:
        code, message = problem
        return error(code, message, status_code=400)

    president = CustomUser.objects.filter(phone=phone, society__isnull=False).first()
    if not president:
        return error('ACCOUNT_NOT_FOUND', 'No society account is linked to this phone', status_code=404)

    president.is_phone_verified = True
    president.resident_status = CustomUser.ResidentStatus.VERIFIED
    president.save(update_fields=['is_phone_verified', 'resident_status', 'updated_at'])

    from apps.users.tokens import issue_tokens

    return ok(
        {
            **issue_tokens(president),
            'user': UserSerializer(president).data,
            'society': SocietyPublicSerializer(president.society).data,
        }
    )


# ── Lookup ────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([AllowAny])
def society_public_detail(request, subdomain):
    """Public existence check used by the login/onboarding screens (§4.2)."""
    society = Society.objects.filter(subdomain__iexact=subdomain.strip().lower()).first()
    if not society:
        return error('SOCIETY_NOT_FOUND', 'Society not found', status_code=404)
    return ok(SocietyPublicSerializer(society).data)


@api_view(['GET'])
@permission_classes([AllowAny])
def search_societies(request):
    """Let residents find their society by name or pincode during onboarding."""
    search = (request.query_params.get('search') or '').strip()
    societies = Society.objects.exclude(
        subscription_status=Society.OnboardingStatus.SUSPENDED
    ).order_by('name')
    if search:
        societies = societies.filter(Q(name__icontains=search) | Q(subdomain__icontains=search) | Q(pincode=search))
    return paginated(societies, request, lambda rows: SocietyPublicSerializer(rows, many=True).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasSociety])
def my_society(request):
    """The caller's own society, with trial and registry counters."""
    return ok(SocietyDetailSerializer(request.user.society).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsPlatformAdmin])
def list_societies(request):
    """Cross-tenant listing for platform staff only (superadmin console is Phase 6)."""
    societies = Society.objects.order_by('-created_at')
    search = (request.query_params.get('search') or '').strip()
    if search:
        societies = societies.filter(
            Q(name__icontains=search) | Q(subdomain__icontains=search) | Q(city__icontains=search)
        )
    status_filter = request.query_params.get('status')
    if status_filter:
        societies = societies.filter(subscription_status=status_filter)
    return paginated(societies, request, lambda rows: SocietyDetailSerializer(rows, many=True).data)


# ── Subscription (B2B portal, doc 04 §11) ─────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated, HasSociety])
def subscription_status(request):
    """Current plan, trial state and next due date (§11.1)."""
    society = request.user.society
    subscription, _ = SocietySubscription.objects.get_or_create(
        society=society,
        defaults={
            'tier': society.subscription_tier,
            'base_amount': settings.STARTER_MONTHLY_FEE,
            'status': SocietySubscription.PaymentStatus.PENDING,
        },
    )
    return ok(SocietySubscriptionSerializer(subscription).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsRWAAdmin, HasSociety])
def subscription_checkout(request):
    """Start the flat-rate subscription payment for the next cycle (§11.2).

    Phase 1 has no gateway: like resident payments, this returns a UPI intent
    deep link and the treasurer confirms the transfer manually (§11.3 below).
    Automated webhook reconciliation is Phase 4b (doc 07).
    """
    cycle = (request.data.get('billing_cycle') or 'MONTHLY').upper()
    if cycle != SocietySubscription.BillingCycle.MONTHLY:
        return validation_error({'billing_cycle': ['Phase 1 supports MONTHLY only']})

    society = request.user.society
    subscription, _ = SocietySubscription.objects.get_or_create(
        society=society,
        defaults={
            'tier': society.subscription_tier,
            'base_amount': settings.STARTER_MONTHLY_FEE,
            'status': SocietySubscription.PaymentStatus.PENDING,
        },
    )
    if subscription.status == SocietySubscription.PaymentStatus.PAID:
        return error('ALREADY_PAID', 'The current subscription cycle is already paid', status_code=409)

    period = date.today().strftime('%Y-%m')
    note = f'SUB-{society.subdomain}-{period}'
    link = build_upi_link(
        amount=subscription.base_amount,
        transaction_note=note,
        payee_vpa=settings.UPI_PAYEE_VPA,
        payee_name=settings.UPI_PAYEE_NAME,
    )

    subscription.invoice_date = date.today()
    subscription.due_date = date.today() + timedelta(days=7)
    subscription.save(update_fields=['invoice_date', 'due_date'])

    return ok(
        {
            'upi_link': link,
            'payment_url': link,
            'amount': str(subscription.base_amount),
            'billing_cycle': subscription.billing_cycle,
            'reference_note': note,
            'due_date': subscription.due_date,
            'expires_in': settings.UPI_INTENT_TTL_SECONDS,
        }
    )


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsRWAAdmin, HasSociety])
def subscription_confirm(request):
    """Treasurer confirms the bank transfer; the tenant goes ACTIVE (§11.3 stand-in).

    Phase 1 has no gateway webhook to receive, so activation is a manual,
    audited step — the same MVR pattern used for resident payments.
    """
    utr = (request.data.get('utr_number') or '').strip()
    if not utr:
        return validation_error({'utr_number': ['UTR number from the payment app is required']})

    society = request.user.society
    with transaction.atomic():
        subscription = SocietySubscription.objects.filter(society=society).first()
        if not subscription:
            subscription = SocietySubscription.objects.create(
                society=society,
                tier=society.subscription_tier,
                base_amount=settings.STARTER_MONTHLY_FEE,
            )
        subscription.status = SocietySubscription.PaymentStatus.PAID
        subscription.paid_date = date.today()
        subscription.save(update_fields=['status', 'paid_date'])

        society.subscription_status = Society.OnboardingStatus.ACTIVE
        society.save(update_fields=['subscription_status', 'updated_at'])

    logger.info(
        'Subscription activated for %s by %s (UTR %s)', society.subdomain, request.user.phone, utr
    )
    return ok(
        {
            'society_status': society.subscription_status,
            'subscription_status': subscription.status,
            'paid_date': subscription.paid_date,
            'message': 'Subscription is active. Thank you.',
        }
    )
