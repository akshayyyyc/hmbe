"""JWT issuance helpers.

Tenant and role claims are embedded so downstream consumers (REST middleware,
and in W5 the WebSocket auth layer) can scope a connection without a second
round trip to the database.
"""

from rest_framework_simplejwt.tokens import RefreshToken


def issue_tokens(user):
    """Return ``{'access', 'refresh'}`` for ``user``, with tenant/role claims."""
    refresh = RefreshToken.for_user(user)
    refresh['role'] = user.role
    if user.society_id:
        refresh['society_id'] = user.society_id
    if user.is_rwa_admin:
        refresh['is_rwa_admin'] = True
    return {'access': str(refresh.access_token), 'refresh': str(refresh)}
