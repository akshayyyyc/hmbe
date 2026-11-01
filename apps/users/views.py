"""Authentication (OTP + JWT) and user management endpoints (doc 04 §3, §5)."""

import logging

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Q
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.pagination import paginated
from apps.core.permissions import HasSociety, IsRWAAdmin, IsRWACommittee
from apps.core.responses import error, ok, validation_error

from .models import CustomUser
from .serializers import (
    AssignSocietySerializer,
    ProfileUpdateSerializer,
    SendOtpSerializer,
    UserListQuerySerializer,
    UserSerializer,
    VerifyOtpSerializer,
)
from .tokens import issue_tokens as _token_pair
from .utils import (
    consume_otp,
    create_otp,
    deliver_otp,
    seconds_until_resend_allows,
)

logger = logging.getLogger(__name__)


# ── Public: OTP authentication ────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([AllowAny])
def send_otp(request):
    """Issue a 6-digit OTP for login/registration; store only its hash (§3.1)."""
    serializer = SendOtpSerializer(data=request.data)
    if not serializer.is_valid():
        return validation_error(serializer.errors)

    phone = serializer.validated_data['phone']
    purpose = serializer.validated_data['purpose']

    cooldown = seconds_until_resend_allows(phone)
    if cooldown:
        return ok(
            {'message': 'Please wait before requesting a new OTP', 'retry_after': cooldown},
            status_code=429,
        )

    _record, code = create_otp(phone=phone, purpose=purpose)
    deliver_otp(phone, code)

    return ok({'message': 'OTP sent successfully', 'expires_in': settings.OTP_EXPIRY_SECONDS})


@api_view(['POST'])
@permission_classes([AllowAny])
def verify_otp_and_login(request):
    """Verify an OTP, create the resident if new, and return JWT tokens (§3.2)."""
    serializer = VerifyOtpSerializer(data=request.data)
    if not serializer.is_valid():
        return validation_error(serializer.errors)

    phone = serializer.validated_data['phone']
    purpose = serializer.validated_data['purpose']

    valid, problem = consume_otp(phone=phone, otp=serializer.validated_data['otp'], purpose=purpose)
    if not valid:
        code, message = problem
        return error(code, message, status_code=400)

    user, created = CustomUser.objects.get_or_create(
        phone=phone,
        defaults={
            'username': phone,
            'role': CustomUser.UserRole.RESIDENT,
            'resident_status': CustomUser.ResidentStatus.PENDING,
            'is_phone_verified': True,
        },
    )
    if not created and not user.is_phone_verified:
        user.is_phone_verified = True
        user.save(update_fields=['is_phone_verified', 'updated_at'])
    if created:
        logger.info('New user self-registered via OTP: %s', phone)

    payload = {**_token_pair(user), 'user': UserSerializer(user).data, 'is_new_user': created}
    return ok(payload)


# ── Public: token lifecycle ───────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([AllowAny])
def refresh_token(request):
    """Exchange a refresh token for a new pair, blacklisting the old one (§3.3)."""
    raw = request.data.get('refresh')
    if not raw:
        return error('MISSING_REFRESH', 'Refresh token is required', status_code=400)

    try:
        old = RefreshToken(raw)
        # ``Token.user`` is not part of simplejwt's public surface, so the user is
        # resolved from the id claim the library always writes (``user_id``).
        user = CustomUser.objects.get(pk=old[api_settings.USER_ID_CLAIM])
        # Rotation makes the presented token single-use, so a stolen refresh
        # token stops working the moment the real client rotates it.
        old.blacklist()
        tokens = _token_pair(user)
    except (InvalidToken, TokenError, ObjectDoesNotExist, KeyError):
        return error('INVALID_REFRESH', 'Invalid or blacklisted refresh token', status_code=401)

    return ok(tokens)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def logout(request):
    """Revoke the supplied refresh token so it cannot be replayed (§3.4)."""
    raw = request.data.get('refresh')
    if not raw:
        return error('MISSING_REFRESH', 'Refresh token is required', status_code=400)

    try:
        RefreshToken(raw).blacklist()
    except (InvalidToken, TokenError, AttributeError):
        return error('INVALID_REFRESH', 'Invalid refresh token', status_code=400)

    return ok({'message': 'Logged out successfully'})


# ── Authenticated: profile ────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def current_user(request):
    """The caller's own profile, with their society (§5.1)."""
    return ok(UserSerializer(request.user).data)


@api_view(['PUT', 'PATCH'])
@permission_classes([IsAuthenticated])
def update_current_user(request):
    """Self-service profile edit. Phone changes go through OTP re-verification."""
    if 'phone' in request.data:
        return error('FORBIDDEN', 'Phone number cannot be changed here', status_code=403)

    serializer = ProfileUpdateSerializer(request.user, data=request.data, partial=True)
    if not serializer.is_valid():
        return validation_error(serializer.errors)
    serializer.save()
    return ok(UserSerializer(request.user).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def assign_to_society(request):
    """Link an OTP-registered user to a society by proving they hold a flat (§5.2).

    The flat must already exist in the society's unit registry, which is why
    this runs after the RWA has imported units (W3) — a self-registered resident
    without a matching flat stays ``UNASSIGNED`` and sees no tenant data.
    """
    user = request.user
    if user.society_id:
        return error('ALREADY_ASSIGNED', 'User is already associated with a society', status_code=400)

    serializer = AssignSocietySerializer(data=request.data)
    if not serializer.is_valid():
        return validation_error(serializer.errors)

    from apps.society.models import Society
    from apps.units.models import Unit

    subdomain = serializer.validated_data['society_subdomain'].strip().lower()
    flat_number = serializer.validated_data['flat_number'].strip()

    society = Society.objects.filter(subdomain=subdomain).first()
    if not society:
        return error('SOCIETY_NOT_FOUND', 'Society not found', status_code=404)
    if society.subscription_status == society.OnboardingStatus.SUSPENDED:
        return error('SOCIETY_SUSPENDED', 'This society is not active', status_code=403)

    parts = [p.strip() for p in flat_number.split('-') if p.strip()]
    unit_number = parts[-1]
    building = parts[0] if len(parts) > 1 else None

    # "A-101", "101" and "a-101" all refer to the same flat.
    candidates = Unit.objects.filter(society=society, is_deleted=False)
    if building:
        candidates = candidates.filter(building__iexact=building)
    unit = candidates.filter(unit_number__iexact=unit_number).first()
    if not unit:
        return error('FLAT_NOT_FOUND', 'Flat number not found in this society', status_code=404)

    user.society = society
    user.flat_number = unit.unit_number
    user.resident_status = CustomUser.ResidentStatus.VERIFIED
    user.save(update_fields=['society', 'flat_number', 'resident_status', 'updated_at'])

    return ok(UserSerializer(user).data)


# ── Authenticated: directories ────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated, IsRWAAdmin, HasSociety])
def list_users(request):
    """Paginated user directory for RWA admins, scoped to their society (§5.3)."""
    query = UserListQuerySerializer(data=request.query_params)
    if not query.is_valid():
        return validation_error(query.errors)

    users = CustomUser.objects.filter(society=request.user.society).order_by('phone')
    if query.validated_data.get('role'):
        users = users.filter(role=query.validated_data['role'])
    if query.validated_data.get('resident_status'):
        users = users.filter(resident_status=query.validated_data['resident_status'])
    search = query.validated_data.get('search')
    if search:
        users = users.filter(
            Q(first_name__icontains=search)
            | Q(last_name__icontains=search)
            | Q(phone__icontains=search)
            | Q(flat_number__icontains=search)
        )

    return paginated(users, request, lambda rows: UserSerializer(rows, many=True).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsRWACommittee, HasSociety])
def list_residents(request):
    """Verified residents — the Phase 1 emergency contact directory (§5.4)."""
    residents = (
        CustomUser.objects.filter(
            society=request.user.society,
            resident_status=CustomUser.ResidentStatus.VERIFIED,
            role__in=[CustomUser.UserRole.RESIDENT, CustomUser.UserRole.LANDLORD],
        )
        .order_by('flat_number', 'first_name')
    )
    payload = [
        {
            'id': u.id,
            'name': u.get_full_name() or u.username,
            'phone': u.phone,
            'flat_number': u.flat_number,
            'role': u.role,
        }
        for u in residents
    ]
    return ok(payload)
