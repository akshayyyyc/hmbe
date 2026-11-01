"""WebSocket URL registration for ``backend/asgi.py``.

The W1 stub lived in ``views.py``; keeping the module here means the ASGI app
imports cleanly today, and W5 (doc 08) replaces the stub consumer below with
``consumers.ChatConsumer`` without touching ``asgi.py``.
"""

from django.urls import path

from .consumers import ChatConsumer

websocket_urlpatterns = [
    path('ws/chat/<int:room_id>/', ChatConsumer.as_asgi()),
]

__all__ = ['websocket_urlpatterns']
