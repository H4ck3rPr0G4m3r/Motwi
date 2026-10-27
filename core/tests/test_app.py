import asyncio
import io
import json
import logging

import httpx
import pytest
import respx
from conftest import RecordingExecutor

from motwi.app import MotwiApp, print_events, resolve_ai, seed_if_empty
from motwi.live import LiveConfig
from motwi.models import Kind, StreamContext
from motwi.paths import db_path
from motwi.runner import run_live, subscriptions
from motwi.settings import ProviderConfig, Settings
from motwi.sim import fake_llm
from motwi.sim.fake_llm import FakeLLMClient
from motwi.store import Store
from motwi.twitch.helix import HELIX
from motwi.twitch.tokens import MemorySecrets, Token, TokenStore

FAR = 10_000_000_000.0
FAKE = ProviderConfig("custom", "fake-llm", base_url="http://fake.invalid")
TS = "2026-10-27T12:00:00Z"


def chat_event(n: int, text: str, login: str = "grizzlecat", **extra) -> dict:
    return {
        "broadcaster_user_id": "42",
        "chatter_user_id": f"id-{login}",
        "chatter_user_login": login,
        "chatter_user_name": login,
        "message_id": f"msg-{n}",
        "message": {"text": text, "fragments": [{"type": "text", "text": text}]},
        "badges": [],
        **extra,
    }


@pytest.fixture
async def app():
    store = await Store.open(":memory:")
    await seed_if_empty(store)
    await store.save_settings(Settings(primary=FAKE, batch_wait_s=0.2))
    live = await LiveConfig.load(store)
    executor = RecordingExecutor()
    fake = FakeLLMClient()
    a = MotwiApp(
        store=store,
        live=live,
        executor=executor,
        clients=lambda cfg: fake,
        self_user_ids={"id-motwibot"},
    )
    a.executor = executor
    yield a
    await a.aclose()
    await store.close()


async def _say(app, n, text, login="grizzlecat"):
    app.on_event("channel.chat.message", chat_event(n, text, login), TS)
    await app.drain()


def _timeouts(executor) -> list[int]:
    return [c[2] for c in executor.calls if c[0] == "timeout"]


async def test_chat_event_runs_through_pipeline_to_executor(app):
    await _say(app, 1, "ur trash, uninstall")
    assert ("delete", "msg-1") in app.executor.calls
    assert _timeouts(app.executor) == [60]


async def test_own_bot_and_shared_chat_messages_are_ignored(app):
    await _say(app, 1, "ur trash, uninstall", login="motwibot")
    app.on_event(
        "channel.chat.message",
        chat_event(2, "ur trash, uninstall", source_broadcaster_user_id="1000"),
        TS,
    )
    await app.drain()
    assert app.executor.calls == []


async def test_stream_offline_resets_escalation(app):
    await _say(app, 1, "ur trash, uninstall")
    await _say(app, 2, "you are braindead, uninstall")
    app.on_event("stream.offline", {"broadcaster_user_id": "42"}, TS)
    await _say(app, 3, "ur trash, uninstall")
    assert _timeouts(app.executor) == [60, 600, 60]


async def test_channel_update_and_online_change_stream_context(app):
    app.on_event("channel.update", {"title": "Blind run", "category_name": "Elden Ring"}, TS)
    app.on_event("stream.online", {"id": "s-77", "type": "live"}, TS)
    assert app.live.stream == StreamContext("Blind run", "Elden Ring", "s-77")


async def test_failed_message_handling_is_logged_not_lost(app, monkeypatch, caplog):
    async def boom(msg):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(app.pipeline, "handle", boom)
    with caplog.at_level(logging.ERROR):
        await _say(app, 1, "hello")
    assert "handling message msg-1 failed" in caplog.text


async def test_housekeeping_expires_and_purges_then_sleeps(app, monkeypatch):
    calls = []

    async def expire():
        calls.append("expire")
        return []

    async def purge(ts):
        calls.append("purge")
        return 0

    monkeypatch.setattr(app.moderation, "expire_overdue", expire)
    monkeypatch.setattr(app.store, "purge", purge)

    async def stop(_):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await app.housekeeping(sleep=stop)
    assert calls == ["expire", "purge"]


async def test_print_events_shows_actions(app):
    out = io.StringIO()
    printer = asyncio.create_task(print_events(app.bus, out))
    await asyncio.sleep(0)
    await _say(app, 1, "ur trash, uninstall")
    await asyncio.sleep(0.01)
    printer.cancel()
    assert "grizzlecat" in out.getvalue() and "-> done" in out.getvalue()


# AI resolution


def test_resolve_ai_uses_env_provider_and_keyring_key():
    secrets = MemorySecrets()
    secrets.set("ai:primary", "k-stored")
    settings, keys = resolve_ai(Settings(), {"MOTWI_AI_PROVIDER": "openrouter"}, secrets)
    assert settings.primary.provider == "openrouter"
    assert keys[settings.primary] == "k-stored"


def test_resolve_ai_env_key_wins_and_backup_without_key_is_dropped():
    secrets = MemorySecrets()
    secrets.set("ai:primary", "k-stored")
    backup = ProviderConfig("openrouter", "m")
    settings, keys = resolve_ai(
        Settings(primary=FAKE, backup=backup), {"MOTWI_AI_KEY": "k-env"}, secrets
    )
    assert keys == {FAKE: "k-env"}
    assert settings.backup is None


def test_resolve_ai_without_key_explains():
    with pytest.raises(ValueError, match="motwi set-key"):
        resolve_ai(Settings(), {}, MemorySecrets())


async def test_seed_only_on_first_run():
    store = await Store.open(":memory:")
    try:
        assert await seed_if_empty(store) is True
        assert await seed_if_empty(store) is False
    finally:
        await store.close()


def test_subscriptions_read_chat_as_moderator_account():
    subs = {t: (v, c) for t, v, c in subscriptions("42", "99")}
    assert subs["channel.chat.message"] == ("1", {"broadcaster_user_id": "42", "user_id": "99"})
    assert set(subs) == {
        "channel.chat.message",
        "channel.update",
        "stream.online",
        "stream.offline",
    }


# end to end: fake Twitch (HTTP + WebSocket) and a fake AI provider over HTTP


class FakeSocket:
    def __init__(self) -> None:
        self.inbox: asyncio.Queue = asyncio.Queue()

    async def recv(self):
        return json.dumps(await self.inbox.get())

    async def close(self) -> None:
        pass


def _ai_reply(request: httpx.Request) -> httpx.Response:
    document = json.loads(json.loads(request.content)["messages"][1]["content"])
    results = []
    for m in document["messages"]:
        score, rule, reason = fake_llm._score(m["text"], document["rules"])
        results.append({"id": m["id"], "score": score, "rule": rule, "reason": reason})
    content = json.dumps({"results": results})
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


@respx.mock
async def test_run_live_moderates_a_channel_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTWI_HOME", str(tmp_path))
    monkeypatch.setenv("MOTWI_TWITCH_CLIENT_ID", "cid")
    store = await Store.open(db_path())
    await store.save_settings(Settings(batch_wait_s=0.2))
    await store.close()

    secrets = MemorySecrets()
    TokenStore(secrets).save("streamer", Token("acc", "ref", FAR, (), "42", "streamer"))
    secrets.set("ai:primary", "k")

    respx.get(f"{HELIX}/channels").respond(200, json={"data": [{"title": "t", "game_name": "g"}]})
    respx.get(f"{HELIX}/streams").respond(200, json={"data": [{"id": "s1"}]})
    subs = respx.post(f"{HELIX}/eventsub/subscriptions").respond(202, json={"data": [{}]})
    deletes = respx.delete(f"{HELIX}/moderation/chat").respond(204)
    bans = respx.post(f"{HELIX}/moderation/bans").respond(200, json={"data": [{}]})
    respx.post("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions").mock(
        side_effect=_ai_reply
    )

    sock = FakeSocket()
    sock.inbox.put_nowait(
        {
            "metadata": {"message_id": "w", "message_type": "session_welcome"},
            "payload": {"session": {"id": "sess", "keepalive_timeout_seconds": 30}},
        }
    )

    async def connect(url):
        return sock

    out = io.StringIO()
    task = asyncio.create_task(run_live(secrets=secrets, env={}, out=out, connect=connect))
    async with asyncio.timeout(5):
        while subs.call_count < 4:
            await asyncio.sleep(0.01)
        sock.inbox.put_nowait(
            {
                "metadata": {
                    "message_id": "n1",
                    "message_type": "notification",
                    "message_timestamp": TS,
                },
                "payload": {
                    "subscription": {"type": "channel.chat.message"},
                    "event": chat_event(1, "ur trash, uninstall"),
                },
            }
        )
        while bans.call_count < 1:
            await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert deletes.calls[0].request.url.params["message_id"] == "msg-1"
    ban = json.loads(bans.calls[0].request.content)["data"]
    assert (ban["user_id"], ban["duration"]) == ("id-grizzlecat", 60)
    assert "Motwi is watching #streamer as streamer" in out.getvalue()
    store = await Store.open(db_path())
    try:
        actions = await store.list_actions(limit=5)
        assert [a.kind for a in actions] == [Kind.TIMEOUT]
        assert actions[0].stream_id == "s1"
    finally:
        await store.close()


async def test_run_live_without_account_says_reconnect(tmp_path, monkeypatch):
    from motwi.twitch.auth import AuthExpired

    monkeypatch.setenv("MOTWI_HOME", str(tmp_path))
    monkeypatch.setenv("MOTWI_TWITCH_CLIENT_ID", "cid")
    with pytest.raises(AuthExpired, match="no Twitch streamer account"):
        await run_live(secrets=MemorySecrets(), env={}, out=io.StringIO())
