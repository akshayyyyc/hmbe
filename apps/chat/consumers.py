"""WebSocket consumer for society-wide community chat (doc 08 §3, W5).

Wire protocol — as-built, and shared by both clients (hmfe ``useWebSocket.ts``
and hmma ``use-websocket.ts``):

  connect   ``/ws/chat/<room_id>/?access=<JWT access token>``
            (query param because neither browser nor RN WebSocket handshakes
            can set an Authorization header; the param name is the one the
            shipped frontends already send)
  send      ``{"type": "chat_message", "content": "text"}``
            ``{"type": "pong"}``                  — reply to the server PING
  receive   ``{"type": "chat_message", "id", "room", "content",
               "sender": {"id", "name", "role"}, "created_at"}``
            ``{"type": "error", "message": "..."}``
            ``{"type": "ping"}``                  — every 30 s (§3.4)

The broadcast payload is ``ChatMessageSerializer`` output plus a ``type``
discriminator, so a live frame and a REST history row are the same shape and
clients merge them without branching. Names never phones go on the wire
(CHAT-006); ``created_at`` comes from the DB row, which is also the
reconnection cursor (doc 08 §6).

Divergences from the doc 08 sketch, deliberate and recorded in the doc:

* Join/leave notifications are not broadcast. In a 300-flat society every
  app foreground would spam the room, and the sketch's connection-count code
  was a placeholder that never worked. Online presence is Phase 3 territory.
* ``group_add``/``group_discard`` maintain group membership — the sketch's
  ``group_send`` on connect notifies without ever joining, so its own
  broadcasts would have gone nowhere.
* ``AccessToken`` rather than ``UntypedToken``: the type claim is checked, so
  a long-lived refresh token cannot authenticate a socket.

Tenant isolation (doc 08 §5): the room is resolved from the *caller's*
society and a mismatching path id closes the socket, and the Redis group name
carries the society id — so a forged ``room_id`` cannot join another
society's fan-out even through the channel layer.
"""

import asyncio
import json
import logging
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken

from .models import ChatMessage
from .serializers import ChatMessageSerializer

logger = logging.getLogger(__name__)

User = get_user_model()

MAX_MESSAGE_LENGTH = 2000  # doc 08 §3.3; per-IP rate limits deferred to Phase 2 (§5)
HEARTBEAT_INTERVAL_SECONDS = 30  # PRD §2, doc 08 §3.4 — client hooks answer PONG

# Custom close codes (doc 08 §3.3; 4004 is new — the room check this build adds).
# The 4404 CHAT_NOT_IMPLEMENTED stub policy is retired: sockets are accepted or
# closed for a documented reason.
CLOSE_INVALID_TOKEN = 4001
CLOSE_UNKNOWN_USER = 4002
CLOSE_NO_SOCIETY = 4003
CLOSE_ROOM_UNAVAILABLE = 4004


class ChatConsumer(AsyncWebsocketConsumer):
    """One socket per client, joined to its society's chat group."""

    async def connect(self):
        # ── Authenticate: JWT access token as a query parameter (§2) ──
        query_string = self.scope.get('query_string', b'').decode('utf-8')
        token = (parse_qs(query_string).get('access') or [''])[0]
        if not token:
            await self.close(code=CLOSE_INVALID_TOKEN)
            return

        try:
            validated = AccessToken(token)
        except TokenError:
            await self.close(code=CLOSE_INVALID_TOKEN)
            return

        self.user = await self._get_user(validated.get('user_id'))
        if self.user is None:
            await self.close(code=CLOSE_UNKNOWN_USER)
            return

        # ── Resolve the room: it must be a room of *this* user's society (§5) ──
        self.room = None
        if self.user.society_id:
            requested = self.scope.get('url_route', {}).get('kwargs', {}).get('room_id')
            self.room = await self._get_room(self.user, requested)
        if self.room is None:
            # No society assigned (4003) vs no usable room for it (4004).
            await self.close(code=4003 if not self.user.society_id else CLOSE_ROOM_UNAVAILABLE)
            return

        self.room_group_name = f'chat_society_{self.room.society_id}'
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

        # Heartbeat per doc 08 §3.4: PING every 30 s; the nginx edge idles at 7 d
        # (deploy/nginx/snippets/ws.conf), this keeps the app-level view honest.
        self._ping_task = asyncio.ensure_future(self._heartbeat_loop())

    async def disconnect(self, close_code):
        ping_task = getattr(self, '_ping_task', None)
        if ping_task is not None:
            ping_task.cancel()
        if hasattr(self, 'room_group_name'):
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    async def _heartbeat_loop(self):
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
                await self.send(text_data=json.dumps({'type': 'ping'}))
        except asyncio.CancelledError:
            pass
        except Exception:
            # The socket is already gone; Channels runs disconnect() around this.
            logger.debug('chat heartbeat stopped for channel %s', self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        if not text_data:
            return

        try:
            data = json.loads(text_data)
        except (ValueError, TypeError):
            await self._send_error('Malformed message; expected JSON.')
            return

        if not isinstance(data, dict):
            await self._send_error('Malformed message; expected a JSON object.')
            return

        if data.get('type') == 'pong':
            return  # heartbeat acknowledgement — nothing to act on in Phase 1

        # Truncate before the strip/length check so an oversized frame cannot
        # even reach the DB attempt; channels frame limits sit above this.
        content = str(data.get('content', ''))[: MAX_MESSAGE_LENGTH * 4].strip()
        if not content:
            return
        if len(content) > MAX_MESSAGE_LENGTH:
            await self._send_error(f'Message exceeds maximum length of {MAX_MESSAGE_LENGTH} characters')
            return

        data_out = await self._save_message(content)
        if data_out is None:
            await self._send_error('Message could not be saved. Please retry.')
            return

        await self.channel_layer.group_send(
            self.room_group_name,
            {'type': 'chat.message', 'payload': {'type': 'chat_message', **data_out}},
        )

    async def chat_message(self, event):
        """Fan-out handler: deliver the prebuilt payload to this client."""
        await self.send(text_data=json.dumps(event['payload']))

    # ── DB access (runs in the sync thread pool; handlers stay async-safe) ──

    @database_sync_to_async
    def _get_user(self, user_id):
        if user_id is None:
            return None
        try:
            return User.objects.get(id=user_id, is_active=True)
        except User.DoesNotExist:
            return None

    @database_sync_to_async
    def _get_room(self, user, requested_id):
        """The caller's society room; auto-created for legacy societies like REST does.

        A ``room_id`` in the path that is not the caller's room is a forgery or a
        stale client — close it rather than silently joining the society group.
        """
        from .services import room_for_user

        room = room_for_user(user)  # get_or_create for the society
        if room is None:
            return None
        if requested_id is not None and room.id != requested_id:
            return None
        return room if room.is_active else None

    @database_sync_to_async
    def _save_message(self, content):
        """Persist, then serialize in the same thread-pool hop.

        Serialization stays on the sync side so the async handlers never touch
        the ORM, even if the serializer grows a related field later.
        """
        try:
            message = ChatMessage.objects.create(room=self.room, sender=self.user, content=content)
            return ChatMessageSerializer(message).data
        except Exception:
            logger.exception('failed to persist chat message')
            return None

    async def _send_error(self, message):
        await self.send(text_data=json.dumps({'type': 'error', 'message': message}))
