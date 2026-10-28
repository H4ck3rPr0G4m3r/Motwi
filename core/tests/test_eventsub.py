import asyncio
import json

import pytest

from motwi.twitch import eventsub
from motwi.twitch.convert import chat_message, parse_ts
from motwi.twitch.eventsub import WS_URL, EventSubClient
from motwi.twitch.helix import HelixError


class FakeSocket:
    def __init__(self, url: str) -> None:
        self.url = url
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.closed = False

    def send(self, item) -> None:
        self.inbox.put_nowait(item)

    async def recv(self):
        item = await self.inbox.get()
        if isinstance(item, BaseException):
            raise item
        return json.dumps(item)

    async def close(self) -> None:
        self.closed = True


def welcome(session_id: str, keepalive: float = 30) -> dict:
    return {
        "metadata": {"message_id": f"w-{session_id}", "message_type": "session_welcome"},
        "payload": {"session": {"id": session_id, "keepalive_timeout_seconds": keepalive}},
    }


def notification(message_id: str, sub_type: str = "channel.chat.message", **event) -> dict:
    return {
        "metadata": {
            "message_id": message_id,
            "message_type": "notification",
            "message_timestamp": "2026-10-27T12:00:00.123456789Z",
        },
        "payload": {"subscription": {"type": sub_type}, "event": event},
    }


class Harness:
    def __init__(self, *, fail_connects: int = 0) -> None:
        self.sockets: list[FakeSocket] = []
        self.subscribed: list[str] = []
        self.events: list[tuple] = []
        self.states: list[tuple] = []
        self.sleeps: list[float] = []
        self.fail_connects = fail_connects
        self.subscribe_error: Exception | None = None
        self.client = EventSubClient(
            connect=self.connect,
            subscribe=self.subscribe,
            on_event=lambda t, e, ts: self.events.append((t, e, ts)),
            on_state=lambda s, d: self.states.append((s, d)),
            sleep=self.sleep,
        )

    async def connect(self, url: str) -> FakeSocket:
        if self.fail_connects:
            self.fail_connects -= 1
            raise OSError("network down")
        sock = FakeSocket(url)
        self.sockets.append(sock)
        sock.send(welcome(f"s{len(self.sockets)}"))
        return sock

    async def subscribe(self, session_id: str) -> None:
        if self.subscribe_error:
            raise self.subscribe_error
        self.subscribed.append(session_id)

    async def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        await asyncio.sleep(0)


async def until(cond, timeout: float = 1.0) -> None:
    async with asyncio.timeout(timeout):
        while not cond():
            await asyncio.sleep(0.001)


@pytest.fixture
async def harness():
    h = Harness()
    task = asyncio.create_task(h.client.run())
    h.task = task
    yield h
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_welcome_subscribes_then_delivers_events_once(harness):
    await until(lambda: harness.subscribed == ["s1"])
    sock = harness.sockets[0]
    sock.send(notification("n1", text="hi"))
    sock.send(notification("n1", text="hi"))  # Twitch may redeliver
    sock.send(notification("n2", text="again"))
    await until(lambda: len(harness.events) == 2)
    assert harness.events[0][:2] == ("channel.chat.message", {"text": "hi"})
    assert ("connected", "") in harness.states


async def test_dropped_connection_reconnects_and_resubscribes(harness):
    await until(lambda: harness.subscribed == ["s1"])
    harness.sockets[0].send(ConnectionError("closed 1006"))
    await until(lambda: harness.subscribed == ["s1", "s2"])
    assert harness.sockets[0].closed
    assert harness.sockets[1].url == WS_URL
    assert harness.sleeps == [1.0]


async def test_silent_connection_times_out_and_reconnects(monkeypatch):
    monkeypatch.setattr(eventsub, "KEEPALIVE_GRACE_S", 0.01)
    h = Harness()

    async def connect(url):
        sock = FakeSocket(url)
        h.sockets.append(sock)
        sock.send(welcome(f"s{len(h.sockets)}", keepalive=0.01))
        return sock

    h.client._connect = connect
    task = asyncio.create_task(h.client.run())
    try:
        await until(lambda: len(h.subscribed) >= 2)
    finally:
        task.cancel()
    assert h.sockets[0].closed


async def test_session_reconnect_moves_without_resubscribing(harness):
    await until(lambda: harness.subscribed == ["s1"])
    old = harness.sockets[0]
    old.send(
        {
            "metadata": {"message_id": "r1", "message_type": "session_reconnect"},
            "payload": {"session": {"id": "s1", "reconnect_url": "wss://edge.example/ws"}},
        }
    )
    await until(lambda: len(harness.sockets) == 2 and old.closed)
    new = harness.sockets[1]
    assert new.url == "wss://edge.example/ws"
    new.send(notification("n9", text="after move"))
    await until(lambda: len(harness.events) == 1)
    assert harness.subscribed == ["s1"]
    assert not new.closed


async def test_revocation_is_reported_not_dispatched(harness):
    await until(lambda: harness.subscribed == ["s1"])
    harness.sockets[0].send(
        {
            "metadata": {"message_id": "x1", "message_type": "revocation"},
            "payload": {
                "subscription": {"type": "channel.chat.message", "status": "authorization_revoked"}
            },
        }
    )
    await until(lambda: any(s == "revoked" for s, _ in harness.states))
    assert harness.events == []


async def test_connect_failures_back_off_exponentially():
    h = Harness(fail_connects=3)
    task = asyncio.create_task(h.client.run())
    try:
        await until(lambda: h.subscribed == ["s1"])
    finally:
        task.cancel()
    assert h.sleeps == [1.0, 2.0, 4.0]


async def test_configuration_errors_from_subscribe_propagate():
    h = Harness()
    h.subscribe_error = HelixError(403, "missing scope user:read:chat")
    with pytest.raises(HelixError, match="missing scope"):
        await asyncio.wait_for(h.client.run(), 1)


async def test_transient_subscribe_errors_retry():
    h = Harness()
    h.subscribe_error = HelixError(503, "unavailable")
    task = asyncio.create_task(h.client.run())
    try:
        await until(lambda: len(h.sockets) == 2)
        h.subscribe_error = None
        await until(lambda: len(h.subscribed) == 1)
        assert h.subscribed[0] != "s1"
    finally:
        task.cancel()


# conversion

EVENT = {
    "broadcaster_user_id": "42",
    "chatter_user_id": "7",
    "chatter_user_login": "viewer",
    "chatter_user_name": "Viewer",
    "message_id": "abc",
    "message": {
        "text": "hello Kappa world",
        "fragments": [
            {"type": "text", "text": "hello "},
            {"type": "emote", "text": "Kappa"},
            {"type": "text", "text": " world"},
        ],
    },
    "badges": [{"set_id": "lead_moderator", "id": "1"}, {"set_id": "subscriber", "id": "12"}],
    "source_broadcaster_user_id": None,
}


def test_chat_message_maps_fields_badges_and_emote_free_text():
    msg = chat_message(EVENT, 5.0)
    assert (msg.id, msg.user_id, msg.user_login, msg.display_name) == (
        "abc",
        "7",
        "viewer",
        "Viewer",
    )
    assert msg.text == "hello Kappa world"
    assert msg.emote_free_text == "hello world"
    assert msg.is_mod and "subscriber" in msg.badges
    assert msg.ts == 5.0


def test_shared_chat_messages_from_other_channels_are_skipped():
    assert chat_message({**EVENT, "source_broadcaster_user_id": "1000"}, 0) is None
    assert chat_message({**EVENT, "source_broadcaster_user_id": "42"}, 0) is not None


def test_parse_ts_handles_nanoseconds_and_garbage():
    assert parse_ts("2026-10-27T12:00:00.123456789Z", 0) == pytest.approx(1793102400.123456)
    assert parse_ts("nope", 7.0) == 7.0
