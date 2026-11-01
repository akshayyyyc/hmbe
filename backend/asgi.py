"""
ASGI config — HomeManager backend (Channels entry-point for WebSocket).
"""

import os

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')

from django.core.asgi import get_asgi_application  # noqa: E402

django_app = get_asgi_application()

import apps.chat.routing  # noqa: E402 (side-effect: registers WebSocket routes)

application = ProtocolTypeRouter(
    {
        'http': django_app,
        'websocket': AllowedHostsOriginValidator(
            AuthMiddlewareStack(URLRouter(apps.chat.routing.websocket_urlpatterns))
        ),
    }
)
