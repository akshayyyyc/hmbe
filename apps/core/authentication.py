"""JWT authentication that also resolves the tenant (doc 04 §2.3).

``TenantContextMiddleware`` runs at the Django layer, before DRF has
authenticated anything, so on an API request it can only ever see
AnonymousUser — ``request.society`` stays ``None`` and every view silently
falls back to ``request.user.society``. Serializers that need the tenant during
``validate()`` (the unit registry does) then fail with no tenant at all.

The tenant is stamped here instead: the first point in the request where the
user is genuinely known. Views, serializers and services can all read
``request.society`` and mean it.
"""

from rest_framework_simplejwt.authentication import JWTAuthentication


class TenantJWTAuthentication(JWTAuthentication):
    """``JWTAuthentication`` plus ``request.society``."""

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None
        user, auth = result
        # ``request`` here is DRF's wrapper; the attribute has to land on the
        # underlying HttpRequest so middleware-level readers see the same object.
        request._request.society = getattr(user, 'society', None)
        return user, auth
