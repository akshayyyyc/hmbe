"""
ASGI config — HomeManager backend (Channels entry-point for WebSocket).
"""

import os

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')

from django.core.asgi import get_asgi_application  # noqa: E402

from apps.core.security import AllowedHostsOrNoOriginValidator  # noqa: E402

django_app = get_asgi_application()

import apps.chat.routing  # noqa: E402 (side-effect: registers WebSocket routes)

application = ProtocolTypeRouter(
    {
        'http': django_app,
        # Not channels' AllowedHostsOriginValidator: it rejects a missing Origin,
        # and React Native's WebSocket never sends one. Same host check whenever
        # a browser does send it — see apps/core/security.py.
        'websocket': AllowedHostsOrNoOriginValidator(
            AuthMiddlewareStack(URLRouter(apps.chat.routing.websocket_urlpatterns))
        ),
    }
)
