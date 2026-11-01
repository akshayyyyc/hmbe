"""Tenant context middleware (doc 04 §2.3).

Phase 1 URLs carry no tenant segment (``/api/v1/units/``, not
``/{subdomain}/units/``), so tenancy is resolved from the authenticated user's
``society`` FK. Everything downstream — querysets, serializers, services — reads
``request.society`` instead of trusting client-supplied society ids.

This middleware covers the session-authenticated paths (Django admin, any
``Authorization``-less browser flow) and guarantees the attribute always exists.
For API requests the tenant is stamped by
``apps.core.authentication.TenantJWTAuthentication``, because DRF authenticates
*after* middleware has run — a JWT request would otherwise reach the view with
``request.society`` still ``None``.
"""


class TenantContextMiddleware:
    """Attach ``request.society`` (a ``Society`` or ``None``) to each request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.society = None
        user = getattr(request, 'user', None)
        if user is not None and getattr(user, 'is_authenticated', False):
            # Platform admins have no society; they operate across tenants and
            # scope explicitly via query params.
            request.society = getattr(user, 'society', None)
        return self.get_response(request)
