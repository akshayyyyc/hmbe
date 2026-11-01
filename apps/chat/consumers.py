"""WebSocket consumer placeholder.

This exists so ``backend/asgi.py`` imports cleanly (doc 02 §12 gap #2). Real-time
chat is W5 work — the protocol, JWT handshake and Redis group broadcast are
specified in ``08-websocket-chat.md`` §2–§4, and this file is where they land.

Connecting today closes with policy code 4404 rather than silently accepting a
connection that would never deliver messages, so the mobile client can show the
right state instead of hanging.
"""

from channels.generic.websocket import AsyncWebsocketConsumer

CHAT_NOT_IMPLEMENTED = 4404


class ChatConsumer(AsyncWebsocketConsumer):
    """Rejects chat sockets until W5 implements doc 08."""

    async def connect(self):
        await self.close(code=CHAT_NOT_IMPLEMENTED)

    async def receive(self, text_data=None, bytes_data=None):  # pragma: no cover
        pass
