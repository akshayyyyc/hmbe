"""Role-based permission classes shared across apps (doc 04 §5–§10).

Roles live on ``users.CustomUser.UserRole``; the string values are duplicated
here so that ``apps.core`` stays free of model imports.
"""

from rest_framework.permissions import SAFE_METHODS, BasePermission

RWA_ADMIN_ROLES = frozenset({'RWA_PRESIDENT', 'RWA_SECRETARY', 'RWA_TREASURER'})
# Committee + operational staff — wider than "admin" (e.g. EMD-003 directory).
STAFF_ROLES = frozenset({'SECURITY_GUARD', 'STAFF'})
PLATFORM_ROLES = frozenset({'PLATFORM_ADMIN'})


class IsRWAAdmin(BasePermission):
    """RWA President / Secretary / Treasurer of the caller's society."""

    message = 'This action requires an RWA admin role.'

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role in RWA_ADMIN_ROLES)


class IsRWACommittee(BasePermission):
    """RWA admins plus security guard / staff (directory and gatekeeping views)."""

    message = 'This action requires an RWA, security or staff role.'

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and user.role in (RWA_ADMIN_ROLES | STAFF_ROLES)
        )


class IsPlatformAdmin(BasePermission):
    """HomeManager platform staff — cross-tenant operations only."""

    message = 'This action requires a platform admin role.'

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role in PLATFORM_ROLES)


class HasSociety(BasePermission):
    """Tenant-scoped resources require a society on the account.

    Users who registered by OTP but have not yet linked a flat are rejected here
    with a clear code, rather than 404ing on every scoped query.
    """

    message = 'No society is associated with this account yet.'

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.society_id)


class IsRWAAdminOrReadOnly(BasePermission):
    """Write for RWA admins, read for any authenticated society member."""

    message = 'Only RWA admins can change this resource.'

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated and user.society_id):
            return False
        if request.method in SAFE_METHODS:
            return True
        return user.role in RWA_ADMIN_ROLES
