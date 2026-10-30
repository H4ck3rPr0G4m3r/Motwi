import asyncio
import time

import pytest
import respx
from test_app import TS, FakeSocket, _ai_reply, chat_event

from motwi.events import LoginFinished
from motwi.models import Kind, Status
from motwi.service import FieldError, Motwi, NotConnected
from motwi.settings import ProviderConfig
from motwi.store import Store
from motwi.twitch.auth import ID_BASE
from motwi.twitch.helix import HELIX
from motwi.twitch.tokens import MemorySecrets, Token, TokenStore

FAR = 10_000_000_000.0
GEMINI = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"


def welcome() -> dict:
    return {
        "metadata": {"message_id": "w", "message_type": "session_welcome"},
        "payload": {"session": {"id": "sess", "keepalive_timeout_seconds": 30}},
    }


def notification(n: int, sub_type: str, event: dict) -> dict:
    return {
        "metadata": {
            "message_id": f"n{n}",
            "message_type": "notification",
            "message_timestamp": TS,
        },
        "payload": {"subscription": {"type": sub_type}, "event": event},
    }


@pytest.fixture(autouse=True)
def client_id_env(monkeypatch):
    monkeypatch.setenv("MOTWI_TWITCH_CLIENT_ID", "cid")


@pytest.fixture
def secrets():
    s = MemorySecrets()
    s.set("ai:primary", "k")
    return s


def sign_in(secrets, role="streamer", user_id="42", login="streamer"):
    TokenStore(secrets).save(role, Token("acc", "ref", FAR, (), user_id, login))


@pytest.fixture
def sock():
    s = FakeSocket()
    s.inbox.put_nowait(welcome())
    return s


@pytest.fixture
async def motwi(secrets, sock):
    store = await Store.open(":memory:")

    async def connect(url):
        return sock

    m = await Motwi.open(store, secrets=secrets, env={}, connect=connect)
    yield m
    await m.aclose()
    await store.close()


def fake_twitch(live: bool = True):
    respx.get(f"{HELIX}/channels").respond(
        200, json={"data": [{"title": "Blind run", "game_name": "Elden Ring"}]}
    )
    stream = [{"id": "s1", "started_at": TS}] if live else []
    respx.get(f"{HELIX}/streams").respond(200, json={"data": stream})
    subs = respx.post(f"{HELIX}/eventsub/subscriptions").respond(202, json={"data": [{}]})
    respx.delete(f"{HELIX}/moderation/chat").respond(204)
    bans = respx.post(f"{HELIX}/moderation/bans").respond(200, json={"data": [{}]})
    respx.post(GEMINI).mock(side_effect=_ai_reply)
    return subs, bans


async def until(predicate, timeout=5.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


async def finish_setup(motwi):
    await motwi.update_settings({"setup_complete": True})
    await until(lambda: motwi.twitch_state == "connected")


# status before anything is connected


async def test_fresh_install_is_stopped_and_signed_out(motwi):
    status = await motwi.status()
    assert status["setup_complete"] is False
    assert status["twitch"]["state"] == "signed_out"
    assert status["engine"] == "stopped"
    assert status["accounts"] == {"streamer": None, "bot": None}


async def test_without_ai_key_the_ai_is_off_but_status_explains(secrets, sock):
    secrets.delete("ai:primary")
    store = await Store.open(":memory:")
    m = await Motwi.open(store, secrets=secrets, env={})
    try:
        ai = (await m.status())["ai"]
        assert ai["state"] == "off" and "no AI key" in ai["detail"]
    finally:
        await m.aclose()
        await store.close()


async def test_session_waits_for_setup_to_finish(motwi, secrets):
    sign_in(secrets)
    await motwi.connect()
    assert motwi._session_task is None
    assert motwi.engine_state() == "stopped"


# the live session


@respx.mock
async def test_finishing_setup_connects_and_moderates(motwi, secrets, sock):
    sign_in(secrets)
    subs, bans = fake_twitch()
    await finish_setup(motwi)
    assert subs.call_count == 4
    assert motwi.engine_state() == "watching"
    assert (await motwi.status())["stream"]["live"] is True

    sock.inbox.put_nowait(
        notification(1, "channel.chat.message", chat_event(1, "ur trash, uninstall"))
    )
    await until(lambda: bans.call_count == 1)
    await motwi._session.app.drain()
    await motwi._session.app.flush_counts()
    actions = await motwi.store.list_actions()
    assert [a.kind for a in actions] == [Kind.TIMEOUT]
    assert await motwi.store.count_checked(stream_id="s1") == 1
    streams = await motwi.store.list_streams()
    assert [s.id for s in streams] == ["s1"]


@respx.mock
async def test_paused_ignores_chat(motwi, secrets, sock):
    sign_in(secrets)
    _, bans = fake_twitch()
    await finish_setup(motwi)
    await motwi.set_paused(True)
    assert motwi.engine_state() == "paused"
    sock.inbox.put_nowait(
        notification(1, "channel.chat.message", chat_event(1, "ur trash, uninstall"))
    )
    await asyncio.sleep(0.3)
    assert bans.call_count == 0


@respx.mock
async def test_expired_sign_in_stops_with_reconnect_and_no_loop(motwi, secrets):
    TokenStore(secrets).save("streamer", Token("acc", "ref", 0.0, (), "42", "streamer"))
    refresh = respx.post(f"{ID_BASE}/token").respond(400, json={"message": "Invalid refresh token"})
    await motwi.update_settings({"setup_complete": True})
    await until(lambda: motwi.twitch_state == "reconnect")
    await asyncio.sleep(0.1)
    assert refresh.call_count == 1
    assert motwi.engine_state() == "reconnect"
    warnings = (await motwi.overview())["warnings"]
    assert warnings[0]["kind"] == "twitch" and "Reconnect" in warnings[0]["text"]


@respx.mock
async def test_actions_need_a_connection(motwi):
    with pytest.raises(NotConnected):
        await motwi.act(1, "undo")


@respx.mock
async def test_window_buttons_act_as_the_streamer(motwi, secrets, sock):
    sign_in(secrets)
    _, bans = fake_twitch()
    await finish_setup(motwi)
    sock.inbox.put_nowait(
        notification(1, "channel.chat.message", chat_event(1, "ur trash, uninstall"))
    )
    await until(lambda: bans.call_count == 1)
    await motwi._session.app.drain()
    unban = respx.delete(f"{HELIX}/moderation/bans").respond(204)
    [action] = await motwi.store.list_actions()
    undone = await motwi.act(action.id, "undo")
    assert undone.status is Status.UNDONE and undone.actor == "streamer"
    assert unban.called


# sign-in through the app


@respx.mock
async def test_login_reports_the_result_on_the_bus(motwi, monkeypatch):
    respx.post(f"{ID_BASE}/device").respond(
        200,
        json={
            "device_code": "dc",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://www.twitch.tv/activate",
            "expires_in": 600,
            "interval": 1,
        },
    )
    respx.post(f"{ID_BASE}/token").respond(
        200, json={"access_token": "a", "refresh_token": "r", "expires_in": 3600, "scope": []}
    )
    respx.get(f"{ID_BASE}/validate").respond(
        200, json={"user_id": "42", "login": "streamer", "expires_in": 3600}
    )

    async def no_wait(_):
        return None

    monkeypatch.setattr("motwi.twitch.auth.asyncio.sleep", no_wait)
    q = motwi.bus.subscribe()
    code = await motwi.begin_login("streamer")
    assert code["user_code"] == "ABCD-EFGH"
    async with asyncio.timeout(5):
        while not isinstance(e := await q.get(), LoginFinished):
            pass
    assert (e.ok, e.login) == (True, "streamer")
    assert (await motwi.status())["accounts"]["streamer"]["login"] == "streamer"


async def test_bot_login_needs_the_streamer_first(motwi):
    with pytest.raises(FieldError):
        await motwi.begin_login("bot")


# settings and lists


async def test_settings_reject_bad_values_with_the_field(motwi):
    with pytest.raises(FieldError) as e:
        await motwi.update_settings({"hold_timeout_s": 5})
    assert e.value.field == "hold_timeout_s"
    with pytest.raises(FieldError):
        await motwi.update_settings({"primary": None})


async def test_settings_apply_to_the_live_engine(motwi):
    await motwi.update_settings({"strictness": "strict", "suggest_only": True})
    assert motwi.live.settings.strictness == "strict"
    assert motwi.live.settings.suggest_only is True


async def test_rules_and_people_validate_and_reload(motwi):
    rule = await motwi.add_rule("  No spoilers  ", "delete", "stream")
    assert rule.text == "No spoilers" and rule in motwi.live.rules
    with pytest.raises(FieldError):
        await motwi.add_rule(" ", "delete", "community")
    with pytest.raises(FieldError):
        await motwi.add_rule("x", "nuke", "community")
    await motwi.add_person("vip", "@CoolGuy")
    assert "coolguy" in motwi.live.vips
    with pytest.raises(FieldError):
        await motwi.add_person("vip", "not a name!")


async def test_hidden_blocked_word_is_not_echoed_by_try(motwi):
    await motwi.add_blocked("badword", "timeout", hidden=True)
    result = await motwi.try_message("you badword")
    assert result["reason"] == "Blocked word: (hidden)"
    assert result["action"]["kind"] == "delete"


# AI


@respx.mock
async def test_try_message_uses_the_real_rules_and_decides(motwi):
    respx.post(GEMINI).mock(side_effect=_ai_reply)
    result = await motwi.try_message("ur trash, uninstall")
    assert result["source"] == "ai"
    assert result["score"] >= 6
    assert result["action"]["kind"] in ("warn", "timeout")
    assert await motwi.store.list_actions() == []


@respx.mock
async def test_ai_test_reports_failures_plainly(motwi):
    respx.post(GEMINI).respond(401, json={"error": "bad key"})
    result = await motwi.test_ai("primary", provider="gemini", key="nope")
    assert result["ok"] is False and "401" in result["detail"]


@respx.mock
async def test_ai_test_succeeds_with_a_scoring_reply(motwi):
    respx.post(GEMINI).mock(side_effect=_ai_reply)
    result = await motwi.test_ai("primary", provider="gemini", key="k2")
    assert result["ok"] is True and result["latency_ms"] >= 0


async def test_set_ai_stores_key_and_provider(motwi, secrets):
    config = await motwi.set_ai("primary", "openrouter", None, None, "sk-or")
    assert secrets.get("ai:primary") == "sk-or"
    assert config["primary"]["provider"] == "openrouter"
    assert config["primary"]["has_key"] is True
    assert motwi.live.settings.primary == ProviderConfig(
        "openrouter", "google/gemini-2.5-flash-lite"
    )
    with pytest.raises(FieldError) as e:
        await motwi.set_ai("backup", "custom", "m", None, "k")
    assert e.value.field == "base_url"


async def test_key_change_takes_effect_on_the_next_call(motwi, secrets):
    await motwi.set_ai("primary", "gemini", None, None, "new-key")
    client = motwi._client(motwi.live.settings.primary)
    assert client._api_key == "new-key"


# stats


async def test_stats_fill_every_day_and_label_triggers(motwi):
    from conftest import make_new_action

    from motwi.models import Source

    rule = await motwi.add_rule("No spoilers", "delete", "stream")
    await motwi.store.insert_action(make_new_action(kind=Kind.DELETE, rule_id=rule.id))
    await motwi.store.insert_action(make_new_action(kind=Kind.WARN, status=Status.UNDONE))
    await motwi.store.insert_action(make_new_action(kind=Kind.TIMEOUT, source=Source.SPAM))
    await motwi.store.insert_action(make_new_action(kind=Kind.WARN, status=Status.SUGGESTED))
    await motwi.store.add_checked({(time.strftime("%Y-%m-%d"), None): 40})

    stats = await motwi.stats("7d")
    assert len(stats["per_day"]) == 7
    assert stats["per_day"][-1]["day"] == time.strftime("%Y-%m-%d")
    assert stats["per_day"][-1] | {"day": ""} == {
        "day": "",
        "delete": 1,
        "warn": 1,
        "timeout": 1,
        "ban_request": 0,
    }
    assert stats["totals"]["warn"] == {
        "count": 1,
        "undone": 1,
        "denied": 0,
        "failed": 0,
        "suggested": 1,
    }
    assert stats["checked"] == 40
    labels = {t["label"]: t["count"] for t in stats["triggers"]}
    assert labels == {"No spoilers (stream rule)": 1, "Spam filter": 1, "No specific rule": 1}


async def test_stats_for_last_stream_without_streams_is_empty(motwi):
    assert (await motwi.stats("stream"))["empty"] is True
    with pytest.raises(FieldError):
        await motwi.stats("1y")
