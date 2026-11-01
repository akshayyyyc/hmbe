"""Chat tests: the WebSocket consumer (doc 08 §3) and its REST history integration.

The communicator below is deliberately minimal: it drives the ASGI callable
behind ``ChatConsumer.as_asgi()`` with raw websocket protocol events, which is
exactly what daphne/uvicorn will do in production. ``channels.testing`` cannot
be used here — its package ``__init__`` imports daphne, and daphne is not in
the image (Channels 4 made it optional; we serve with uvicorn instead).
"""

import asyncio
import json
from functools import partial

from channels.db import database_sync_to_async
from channels.routing import URLRouter
from django.test import TransactionTestCase, override_settings

from apps.core.testing import api_client, make_society, make_user
from apps.users.tokens import issue_tokens

from .models import ChatMessage
from .routing import websocket_urlpatterns

# One shared in-memory fan-out layer per test — same instance for every
# communicator, so a group_send from one socket reaches the other.
TEST_CHANNEL_LAYERS = {
    "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"},
}


class WsCommunicator:
    """Drives one websocket connection against the chat URLRouter."""

    def __init__(self, path, query_string="", origin=b"http://localhost", app=None):
        self.app = app if app is not None else URLRouter(websocket_urlpatterns)
        headers = [(b"origin", origin)] if origin is not None else []
        self.scope = {
            "type": "websocket",
            "path": path,
            "path_encoding": "utf-8",
            "query_string": query_string.encode("utf-8"),
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 8000),
            "scheme": "ws",
            "subprotocols": [],
            "cookies": [],
        }
        self._from_client = asyncio.Queue()
        self.incoming = asyncio.Queue()  # frames the server sends us
        self.accepted = False
        self.close_code = None
        self._task = None

    async def _asgi_send(self, event):
        kind = event["type"]
        if kind == "websocket.accept":
            self.accepted = True
        elif kind == "websocket.close":
            self.close_code = event.get("code", 1000)
        elif kind == "websocket.send":
            await self.incoming.put(event.get("text"))

    async def handshake(self):
        """Send websocket.connect, then wait for accept or close."""
        await self._from_client.put({"type": "websocket.connect"})
        self._task = asyncio.create_task(
            self.app(self.scope, self._from_client.get, self._asgi_send)
        )
        for _ in range(200):  # the consumer does JWT + three DB hops before settling
            if self.accepted or self.close_code is not None:
                return
            await asyncio.sleep(0.005)
        raise AssertionError(f'handshake never settled for {self.scope["path"]}')

    async def send_json(self, payload):
        await self._from_client.put(
            {"type": "websocket.receive", "text": json.dumps(payload)}
        )

    async def next_frame(self, timeout=1.0):
        text = await asyncio.wait_for(self.incoming.get(), timeout)
        return json.loads(text)

    async def assert_no_frame(self, timeout=0.05):
        try:
            frame = await self.next_frame(timeout=timeout)
        except asyncio.TimeoutError:
            return
        raise AssertionError(f"unexpected frame: {frame!r}")

    async def disconnect(self):
        if self._task is None:
            return
        if self.accepted:
            await self._from_client.put({"type": "websocket.disconnect", "code": 1000})
        try:
            await asyncio.wait_for(self._task, 1.0)
        except asyncio.TimeoutError:
            self._task.cancel()


@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
class ChatConsumerTests(TransactionTestCase):
    """The whole W5 surface: handshake auth, tenant isolation, broadcast, protocol.

    ``TransactionTestCase``, not ``TestCase``: the consumer's DB hops go through
    ``channels.db.database_sync_to_async``, which calls ``close_old_connections()``
    around every block — and a ``TestCase`` wrapper turns autocommit off, which
    that check treats as a connection to drop. (Same reason channels' own test
    utilities subclass ``TransactionTestCase``.)
    """

    async def _society_pair(self):
        """Two societies, each with one room and two members, plus fresh tokens."""
        society = await database_sync_to_async(make_society)()
        other = await database_sync_to_async(
            partial(make_society, subdomain="moonrise")
        )()
        user_a = await database_sync_to_async(
            partial(make_user, society=society, phone="+91-9800000101")
        )()
        user_b = await database_sync_to_async(
            partial(
                make_user,
                society=society,
                phone="+91-9800000102",
                first="Bearer",
                last="Member",
            )
        )()
        user_other = await database_sync_to_async(
            partial(make_user, society=other, phone="+91-9800000103")
        )()
        room = await database_sync_to_async(lambda: society.chat_room)()
        other_room = await database_sync_to_async(lambda: other.chat_room)()
        access_a = await database_sync_to_async(
            lambda: issue_tokens(user_a)["access"]
        )()
        access_b = await database_sync_to_async(
            lambda: issue_tokens(user_b)["access"]
        )()
        access_other = await database_sync_to_async(
            lambda: issue_tokens(user_other)["access"]
        )()
        refresh_a = await database_sync_to_async(
            lambda: issue_tokens(user_a)["refresh"]
        )()
        return dict(
            society=society,
            other=other,
            user_a=user_a,
            user_b=user_b,
            user_other=user_other,
            room=room,
            other_room=other_room,
            access_a=access_a,
            access_b=access_b,
            access_other=access_other,
            refresh_a=refresh_a,
        )

    def _socket(self, room_id, token=None, origin=b"http://localhost"):
        query = f"access={token}" if token else ""
        return WsCommunicator(f"/ws/chat/{room_id}/", query, origin=origin)

    async def test_missing_token_closes_4001(self):
        ctx = await self._society_pair()
        comm = self._socket(ctx["room"].id)
        await comm.handshake()
        self.assertEqual(comm.close_code, 4001)

    async def test_garbage_token_closes_4001(self):
        ctx = await self._society_pair()
        comm = self._socket(ctx["room"].id, token="not.a.jwt")
        await comm.handshake()
        self.assertEqual(comm.close_code, 4001)

    async def test_refresh_token_cannot_authenticate_a_socket(self):
        """AccessToken (not UntypedToken as doc 08's sketch used): type claim is checked."""
        ctx = await self._society_pair()
        comm = self._socket(ctx["room"].id, token=ctx["refresh_a"])
        await comm.handshake()
        self.assertEqual(comm.close_code, 4001)

    async def test_deactivated_user_closes_4002(self):
        ctx = await self._society_pair()
        await database_sync_to_async(
            partial(setattr, ctx["user_a"], "is_active", False)
        )()
        await database_sync_to_async(ctx["user_a"].save)()
        comm = self._socket(ctx["room"].id, token=ctx["access_a"])
        await comm.handshake()
        self.assertEqual(comm.close_code, 4002)

    async def test_user_without_society_closes_4003(self):
        unassigned = await database_sync_to_async(
            partial(make_user, society=None, phone="+91-9800000104")
        )()
        access = await database_sync_to_async(
            lambda: issue_tokens(unassigned)["access"]
        )()
        comm = self._socket(999999, token=access)
        await comm.handshake()
        self.assertEqual(comm.close_code, 4003)

    async def test_forged_room_id_from_another_society_closes_4004(self):
        """The path room id must be the caller's room — the Redis group never joins."""
        ctx = await self._society_pair()
        comm = self._socket(ctx["other_room"].id, token=ctx["access_a"])
        await comm.handshake()
        self.assertEqual(comm.close_code, 4004)

    async def test_deactivated_room_closes_4004(self):
        ctx = await self._society_pair()
        await database_sync_to_async(
            partial(setattr, ctx["room"], "is_active", False)
        )()
        await database_sync_to_async(ctx["room"].save)()
        comm = self._socket(ctx["room"].id, token=ctx["access_a"])
        await comm.handshake()
        self.assertEqual(comm.close_code, 4004)

    async def test_message_fans_out_to_every_member_with_the_rest_shape(self):
        ctx = await self._society_pair()
        a = self._socket(ctx["room"].id, token=ctx["access_a"])
        b = self._socket(ctx["room"].id, token=ctx["access_b"])
        await a.handshake()
        await b.handshake()
        self.assertTrue(a.accepted and b.accepted)

        await a.send_json(
            {"type": "chat_message", "content": "Parking slot 12 is blocked again"}
        )
        frame_a = await a.next_frame()
        frame_b = await b.next_frame()  # same payload — fan-out is symmetric

        self.assertEqual(frame_a["type"], "chat_message")
        self.assertEqual(frame_a, frame_b)
        self.assertEqual(frame_a["content"], "Parking slot 12 is blocked again")
        self.assertEqual(frame_a["room"], ctx["room"].id)
        # CHAT-006: the full name travels, never the phone number.
        self.assertEqual(
            frame_a["sender"],
            {"id": ctx["user_a"].id, "name": "Test Resident", "role": "RESIDENT"},
        )
        self.assertNotIn("9800000101", json.dumps(frame_a))
        # Same shape as ChatMessageSerializer — the clients merge it into REST history.
        self.assertEqual(
            set(frame_a.keys()),
            {"type", "id", "room", "content", "sender", "created_at"},
        )

        # select_related: the sender/room compare must not lazy-fetch inside the
        # coroutine (SynchronousOnlyOperation) after the wrapper has returned.
        saved = await database_sync_to_async(
            lambda: ChatMessage.objects.select_related("sender", "room").get(
                id=frame_a["id"]
            )
        )()
        self.assertEqual(saved.sender, ctx["user_a"])
        self.assertEqual(saved.room, ctx["room"])

        await a.disconnect()
        await b.disconnect()

    async def test_ws_messages_appear_in_rest_history(self):
        """The persistence + REST fallback pair (doc 08 §2): socket writes, panel reads."""
        ctx = await self._society_pair()
        a = self._socket(ctx["room"].id, token=ctx["access_a"])
        await a.handshake()
        await a.send_json(
            {"type": "chat_message", "content": "Water tank cleaning Sunday 6 AM"}
        )
        await a.next_frame()  # own echo confirms the DB save completed

        body = await database_sync_to_async(
            lambda: api_client(ctx["user_b"]).get("/api/v1/chat/messages/").json()
        )()
        self.assertTrue(body["success"])
        contents = [row["content"] for row in body["data"]]
        self.assertIn("Water tank cleaning Sunday 6 AM", contents)

        await a.disconnect()

    async def test_other_society_cannot_read_the_room(self):
        ctx = await self._society_pair()
        a = self._socket(ctx["room"].id, token=ctx["access_a"])
        await a.handshake()
        await a.send_json(
            {"type": "chat_message", "content": "RWA meeting notes inside"}
        )
        await a.next_frame()

        body = await database_sync_to_async(
            lambda: api_client(ctx["user_other"]).get("/api/v1/chat/messages/").json()
        )()
        contents = [row["content"] for row in body["data"]]
        self.assertNotIn("RWA meeting notes inside", contents)

        await a.disconnect()

    async def test_oversized_message_gets_an_error_frame_and_is_not_saved(self):
        ctx = await self._society_pair()
        a = self._socket(ctx["room"].id, token=ctx["access_a"])
        await a.handshake()
        await a.send_json({"type": "chat_message", "content": "x" * 2001})
        frame = await a.next_frame()
        self.assertEqual(frame["type"], "error")
        count = await database_sync_to_async(ChatMessage.objects.count)()
        self.assertEqual(count, 0)
        await a.disconnect()

    async def test_empty_and_pong_frames_are_ignored(self):
        ctx = await self._society_pair()
        a = self._socket(ctx["room"].id, token=ctx["access_a"])
        await a.handshake()
        await a.send_json({"type": "chat_message", "content": "   "})
        await a.send_json({"type": "pong"})
        await a.assert_no_frame()
        # Still a live connection after the junk:
        await a.send_json({"type": "chat_message", "content": "alive"})
        frame = await a.next_frame()
        self.assertEqual(frame["content"], "alive")
        await a.disconnect()

    async def test_malformed_json_gets_an_error_frame(self):
        ctx = await self._society_pair()
        a = self._socket(ctx["room"].id, token=ctx["access_a"])
        await a.handshake()
        await a._from_client.put(
            {"type": "websocket.receive", "text": "not json at all"}
        )
        frame = await a.next_frame()
        self.assertEqual(frame["type"], "error")
        await a.disconnect()

    async def test_missing_origin_reaches_the_consumer_not_the_deny_gate(self):
        """React Native sends no Origin header; JWT auth in the consumer is the gate.

        With channels' stock ``AllowedHostsOriginValidator`` this connection died
        at the door with a bare 403 (no close code) — the mobile app could never
        connect at all. Here the request reaches the consumer, which rejects it
        with its own 4001 because the token is junk — proving the deny gate let
        it through.
        """
        ctx = await self._society_pair()
        comm = self._socket(ctx["room"].id, token="junk", origin=None)
        await comm.handshake()
        self.assertEqual(comm.close_code, 4001)

    async def test_cross_site_origin_is_denied_before_the_consumer_runs(self):
        """A browser handshake still gets the Origin host check (apps/core/security.py)."""
        from apps.core.security import AllowedHostsOrNoOriginValidator

        ctx = await self._society_pair()
        app = AllowedHostsOrNoOriginValidator(URLRouter(websocket_urlpatterns))
        comm = WsCommunicator(
            f'/ws/chat/{ctx["room"].id}/',
            query_string=f'access={ctx["access_a"]}',
            origin=b"http://evil.example.com",
            app=app,
        )
        await comm.handshake()
        self.assertFalse(comm.accepted)
        # The consumer never ran — none of its JWT/society codes appear; the
        # deny gate closed generically instead.
        self.assertNotIn(comm.close_code, (4001, 4002, 4003, 4004))
        self.assertIsNotNone(comm.close_code)

    async def test_same_origin_browser_handshake_accepted_through_validator(self):
        from apps.core.security import AllowedHostsOrNoOriginValidator

        ctx = await self._society_pair()
        app = AllowedHostsOrNoOriginValidator(URLRouter(websocket_urlpatterns))
        comm = WsCommunicator(
            f'/ws/chat/{ctx["room"].id}/',
            query_string=f'access={ctx["access_a"]}',
            origin=b"http://localhost:25105",
            app=app,
        )
        await comm.handshake()
        self.assertTrue(comm.accepted)
        await comm.disconnect()

    async def test_sockets_survive_reconnect_and_message_order_is_stable(self):
        ctx = await self._society_pair()
        a = self._socket(ctx["room"].id, token=ctx["access_a"])
        b = self._socket(ctx["room"].id, token=ctx["access_b"])
        await a.handshake()
        await b.handshake()
        await a.send_json({"type": "chat_message", "content": "first"})
        await a.send_json({"type": "chat_message", "content": "second"})
        first, second = await b.next_frame(), await b.next_frame()
        self.assertEqual([first["content"], second["content"]], ["first", "second"])
        self.assertLess(first["id"], second["id"])

        # B drops and returns (the mobile foreground pattern): old fan-out is gone.
        await b.disconnect()
        b2 = self._socket(ctx["room"].id, token=ctx["access_b"])
        await b2.handshake()
        self.assertTrue(b2.accepted)
        await a.send_json({"type": "chat_message", "content": "third"})
        self.assertEqual((await b2.next_frame())["content"], "third")
        await a.disconnect()
        await b2.disconnect()
