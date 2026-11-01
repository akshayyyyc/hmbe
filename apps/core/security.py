"""WebSocket transport security shared by all ASGI socket routes.

Channels' ``AllowedHostsOriginValidator`` rejects a *missing* Origin header
(“None is not allowed unless ``*``”). That is right for a browser-only product
and wrong for this one: React Native's WebSocket (OkHttp / NSURLSession) sends
no Origin at all, so every mobile socket would die at the door — a rejection
uvicorn reports only as a bare HTTP 403, verified 2026-11-02.

This validator keeps the browser-facing protection and drops the headerless
ban:

* Browser handshakes always carry Origin → the host must still be in
  ``ALLOWED_HOSTS`` (a cross-site page cannot ride a resident's cookies;
  defence in depth, since chat auth is a query-param JWT, not a cookie).
* Headerless clients (the mobile app, smoke scripts, curl) reach the
  consumer, whose JWT + society + room checks remain the authority
  (doc 08 §5: “validating the JWT, room membership, tenant stamping” stays
  in the app, never at the edge).
"""

from django.conf import settings
from channels.security.websocket import OriginValidator


class NonBrowserFriendlyOriginValidator(OriginValidator):
    """Origin must match ALLOWED_HOSTS when present; absent Origin passes to the consumer."""

    def valid_origin(self, parsed_origin):
        if parsed_origin is None:
            # Non-browser client: no Origin to validate; the JWT handshake decides.
            return True
        return self.validate_origin(parsed_origin)


def AllowedHostsOrNoOriginValidator(application):
    """Drop-in replacement for channels' ``AllowedHostsOriginValidator`` factory."""
    allowed_hosts = settings.ALLOWED_HOSTS
    if settings.DEBUG and not allowed_hosts:
        allowed_hosts = ['localhost', '127.0.0.1', '[::1]']
    return NonBrowserFriendlyOriginValidator(application, allowed_hosts)
