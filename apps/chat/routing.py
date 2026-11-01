"""WebSocket URL registration for ``backend/asgi.py`` (doc 08 §3.2).

The room id is part of the path — both clients fetch it from
``GET /api/v1/chat/rooms/`` before opening a socket, and
``ChatConsumer._get_room`` verifies it belongs to the caller's society
before joining any group. Auth is the ``?access=`` JWT handled inside the
consumer (doc 08 §2: no cookies on WebSocket connections).
"""

from django.urls import path

from .consumers import ChatConsumer

websocket_urlpatterns = [
    path('ws/chat/<int:room_id>/', ChatConsumer.as_asgi()),
]

__all__ = ['websocket_urlpatterns']
