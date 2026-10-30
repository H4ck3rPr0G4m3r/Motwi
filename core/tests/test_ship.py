"""The M5 pieces: recap, update check, start with Windows, and the installer's app jobs."""

import asyncio
import json
import socket
import sys
import time
import uuid

import aiohttp
import httpx
import pytest
import respx
from aiohttp.test_utils import TestClient, TestServer
from conftest import make_new_action

from motwi import __version__
from motwi.api import ApiAuth, create_app, serve
from motwi.autostart import AutoStart, WindowsRunValue
from motwi.events import RecapReady
from motwi.maintenance import self_test, wipe
from motwi.models import Ceiling, Kind, Source, Status
from motwi.recap import build_recap, recap_summary
from motwi.service import Motwi
from motwi.store import Store, Usage
from motwi.twitch.tokens import MemorySecrets, Token, TokenStore
from motwi.updates import (
    DEFAULT_URL,
    Release,
    is_newer,
    parse_release,
    parse_version,
    update_url,
)

START = 1_000_000.0
END = START + 3600


@pytest.fixture
async def store():
    s = await Store.open(":memory:")
    yield s
    await s.close()


@pytest.fixture
async def motwi(store):
    m = await Motwi.open(store, secrets=MemorySecrets(), env={})
    yield m
    await m.aclose()


def action(stream="s1", **kw):
    return make_new_action(stream_id=stream, **kw)


# recap


async def test_recap_counts_triggers_and_cost(store):
    await store.stream_started("s1", START, "blind run", "Elden Ring")
    rule = await store.add_rule("No spoilers", Ceiling.DELETE, "stream")
    await store.insert_action(action(kind=Kind.DELETE, status=Status.DONE, rule_id=rule.id))
    await store.insert_action(action(kind=Kind.DELETE, status=Status.UNDONE, rule_id=rule.id))
    await store.insert_action(
        action(kind=Kind.TIMEOUT, status=Status.DONE, source=Source.SPAM, duration_s=60)
    )
    await store.insert_action(action(kind=Kind.BAN_REQUEST, status=Status.DENIED))
    await store.insert_action(action(stream="other", kind=Kind.WARN, status=Status.DONE))
    await store.add_checked({("1970-01-12", "s1"): 1234})
    await store.record_usage(Usage(START + 5, "gemini", "m", 10, 2, 0.05, 3, "s1"))
    await store.stream_ended("s1", END)

    recap = await build_recap(store, await store.get_stream("s1"))
    assert (recap["title"], recap["game"], recap["ended_at"]) == ("blind run", "Elden Ring", END)
    assert recap["checked"] == 1234
    assert recap["actions"]["delete"]["count"] == 2 and recap["actions"]["delete"]["undone"] == 1
    assert recap["actions"]["ban_request"]["denied"] == 1
    assert recap["actions"]["warn"]["count"] == 0  # another stream's
    labels = {t["label"]: t["count"] for t in recap["triggers"]}
    assert labels["No spoilers (stream rule)"] == 2 and labels["Spam filter"] == 1
    assert recap["cost"] == pytest.approx(0.05)
    json.dumps(recap)  # it gets stored


async def test_an_empty_stream_still_gets_a_recap(store):
    await store.stream_started("s1", START, None, None)
    await store.stream_ended("s1", END)
    recap = await build_recap(store, await store.get_stream("s1"))
    assert recap["checked"] == 0 and recap["escalated"] == [] and recap["triggers"] == []
    assert recap_summary(recap) == "0 messages checked. No actions. AI cost $0.00."


async def test_escalated_chatters_rank_by_how_far_they_got(store):
    await store.stream_started("s1", START, None, None)

    async def offense(login, level, kind=Kind.TIMEOUT, status=Status.DONE, duration=60):
        await store.insert_action(
            action(
                user_id=f"id-{login}",
                user_login=login,
                kind=kind,
                status=status,
                level=level,
                duration_s=duration,
            )
        )

    await offense("once_only", 0, Kind.WARN, duration=None)  # a single first step isn't escalating
    await offense("twice", 0, Kind.WARN, duration=None)
    await offense("twice", 1)
    await offense("worst", 1)
    await offense("worst", 3, Kind.BAN_REQUEST, Status.PENDING, None)
    await offense("forgiven", 2, status=Status.UNDONE)  # undone doesn't count
    rows = await store.top_offenders("s1")
    assert [r["login"] for r in rows] == ["worst", "twice"]
    assert rows[0] == {"login": "worst", "actions": 2, "kind": "ban_request", "duration_s": None}


async def test_outages_are_recorded_and_clipped_to_the_stream(store):
    await store.ai_state_changed("down", "timeouts", START - 100)  # began before the stream
    await store.ai_state_changed("degraded", "on backup", START + 100)
    await store.ai_state_changed("ok", "", START + 200)
    await store.ai_state_changed("down", "again", END - 10)  # still open at the end
    out = await store.outages_between(START, END)
    assert [(o["state"], o["started_at"], o["ended_at"]) for o in out] == [
        ("down", START, START + 100),
        ("degraded", START + 100, START + 200),
        ("down", END - 10, END),
    ]
    assert await store.outages_between(END + 10, END + 20) != []  # the open one carries on
    await store.ai_state_changed("ok", "", END)
    assert await store.outages_between(END + 10, END + 20) == []


def test_the_summary_reads_like_a_sentence():
    recap = {
        "checked": 3210,
        "cost": 0.042,
        "actions": {
            "delete": {"count": 13, "undone": 1, "denied": 0, "failed": 0},
            "warn": {"count": 0, "undone": 0, "denied": 0, "failed": 0},
            "timeout": {"count": 2, "undone": 0, "denied": 0, "failed": 1},
            "ban_request": {"count": 2, "undone": 0, "denied": 1, "failed": 0},
        },
        "outages": [
            {"state": "down", "started_at": 0, "ended_at": 240},
            {"state": "degraded", "started_at": 300, "ended_at": 9000},
        ],
    }
    assert recap_summary(recap) == (
        "3,210 messages checked. 12 deleted, 1 timed out, 1 banned. "
        "The AI was down for 4 minutes. AI cost $0.04."
    )


async def test_a_finished_stream_saves_a_recap_clears_stream_rules_and_notifies(motwi, store):
    await store.stream_started("s1", START, "t", "g")
    await motwi.add_rule("Stream only", "delete", "stream")
    keep = await motwi.add_rule("Always", "delete", "community")
    q = motwi.bus.subscribe()
    await motwi._finish_stream("s1", END)

    assert [r.id for r in await store.list_rules() if r.scope == "stream"] == []
    assert keep.id in {r.id for r in motwi.live.rules}
    assert "Stream only" not in {r.text for r in motwi.live.rules}
    assert (await store.get_stream("s1")).ended_at == END
    saved = await store.last_recap()
    assert saved["stream_id"] == "s1" and saved["seen_ending"] is True
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    assert [e.recap["stream_id"] for e in events if isinstance(e, RecapReady)] == ["s1"]
    assert (await motwi.overview())["last_recap"]["stream_id"] == "s1"


async def test_stream_rules_are_cleared_even_when_the_recap_fails(motwi, store, monkeypatch):
    await store.stream_started("s1", START, "t", "g")
    await motwi.add_rule("Stream only", "delete", "stream")

    async def boom(*a, **k):
        raise RuntimeError("no")

    monkeypatch.setattr("motwi.service.build_recap", boom)
    q = motwi.bus.subscribe()
    await motwi._finish_stream("s1", END)
    assert [r for r in await store.list_rules() if r.scope == "stream"] == []
    assert await store.last_recap() is None
    while not q.empty():
        assert not isinstance(q.get_nowait(), RecapReady)


async def test_a_stream_that_ended_while_closed_is_finished_quietly_and_only_once(motwi, store):
    await store.stream_started("gone", START, "t", "g")
    await store.insert_action(action(stream="gone", kind=Kind.DELETE, status=Status.DONE))
    await store.stream_started("live", START + 10, "t", "g")
    q = motwi.bus.subscribe()
    await motwi._finish_unseen_streams("live")

    assert [s.id for s in await store.open_streams()] == ["live"]
    saved = await store.last_recap()
    assert saved["stream_id"] == "gone" and saved["seen_ending"] is False
    assert saved["ended_at"] == (await store.last_activity("gone"))
    while not q.empty():
        assert not isinstance(q.get_nowait(), RecapReady)

    await motwi._finish_unseen_streams("live")
    assert (await store.last_recap())["stream_id"] == "gone"


async def test_retention_removes_recaps_since_they_name_chatters(store):
    await store.stream_started("s1", START, "t", "g")
    await store.stream_ended("s1", END)
    await store.save_recap("s1", {"stream_id": "s1"})
    await store.ai_state_changed("down", "x", START)
    await store.ai_state_changed("ok", "", START + 5)
    assert await store.purge(START - 1) == 0
    assert await store.purge(END + 1) >= 2
    assert await store.last_recap() is None
    assert await store.outages_between(START, END) == []
    assert (await store.get_stream("s1")).id == "s1"  # the stream itself stays for stats


async def test_twitch_events_finish_the_stream(motwi, store):
    from motwi.app import MotwiApp
    from motwi.twitch.convert import parse_ts

    finished = []

    async def on_end(stream_id, ended_at):
        finished.append((stream_id, ended_at))

    app = MotwiApp(
        store=store,
        live=motwi.live,
        executor=None,
        clients=lambda cfg: None,
        self_user_ids=set(),
        on_stream_end=on_end,
    )
    when = "2026-10-30T20:00:00Z"
    app.on_event("stream.online", {"id": "a", "started_at": when}, when)
    app.on_event("stream.offline", {}, when)
    await app.drain()
    assert finished == [("a", parse_ts(when, 0))]

    # a new stream without the old one's offline event still closes the old one
    app.on_event("stream.online", {"id": "b", "started_at": when}, when)
    app.on_event("stream.online", {"id": "c", "started_at": when}, when)
    await app.drain()
    assert [f[0] for f in finished] == ["a", "b"]
    await app.aclose()


# update check


def test_update_url_can_be_changed_or_turned_off():
    assert update_url({}) == DEFAULT_URL
    assert update_url({"MOTWI_UPDATE_URL": "https://example.test/v.json"}).endswith("v.json")
    assert update_url({"MOTWI_UPDATE_URL": " "}) is None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("1.2.3", (1, 2, 3)),
        ("v1.2", (1, 2, 0)),
        (" v0.10.0 ", (0, 10, 0)),
        ("1.2.3-beta", None),
        ("latest", None),
        (None, None),
        (3, None),
    ],
)
def test_parse_version(text, expected):
    assert parse_version(text) == expected


def test_parse_release_reads_github_and_plain_shapes():
    github = {"tag_name": "v1.2.0", "html_url": "https://github.com/x", "body": "Notes"}
    assert parse_release(github) == Release("1.2.0", "https://github.com/x", "Notes")
    plain = {"version": "1.3", "url": "https://example.test/get", "notes": "Hi"}
    assert parse_release(plain) == Release("1.3", "https://example.test/get", "Hi")


@pytest.mark.parametrize(
    "data",
    [
        {"tag_name": "v9.0.0", "html_url": "http://insecure.test"},
        {"tag_name": "v9.0.0", "html_url": "javascript:alert(1)"},
        {"tag_name": "nightly", "html_url": "https://x.test"},
        {"tag_name": "v9.0.0"},
        {"tag_name": "v9.0.0", "html_url": "https://x.test", "prerelease": True},
        {"tag_name": "v9.0.0", "html_url": "https://x.test", "draft": True},
        ["not", "an", "object"],
    ],
)
def test_unusable_releases_offer_nothing(data):
    assert parse_release(data) is None


def test_newer_compares_numbers_not_text():
    assert is_newer(Release("0.10.0", "u", ""), "0.9.0")
    assert not is_newer(Release("0.9.0", "u", ""), "0.9.0")
    assert not is_newer(Release("0.8.0", "u", ""), "0.9.0")


@respx.mock
async def test_check_offers_a_newer_release_on_overview_but_not_in_chat(motwi):
    respx.get(DEFAULT_URL).mock(
        httpx.Response(200, json={"tag_name": "v99.0.0", "html_url": "https://x.test/r"})
    )
    view = await motwi.check_update()
    assert view["state"] == "available" and view["latest"] == "99.0.0"
    assert (await motwi.status())["update"] == {
        "available": True,
        "version": "99.0.0",
        "url": "https://x.test/r",
    }
    warnings = (await motwi.overview())["warnings"]
    assert [w["url"] for w in warnings if w["kind"] == "update"] == ["https://x.test/r"]
    assert not [w for w in (await motwi.chat_view())["warnings"] if w["kind"] == "update"]


@respx.mock
@pytest.mark.parametrize(
    "response,state",
    [
        (
            httpx.Response(200, json={"tag_name": __version__, "html_url": "https://x.test"}),
            "current",
        ),
        (httpx.Response(404), "unpublished"),
        (httpx.Response(500), "failed"),
        (httpx.Response(200, text="<html>"), "failed"),
        (httpx.Response(200, json={"tag_name": "v9.0.0", "html_url": "http://x"}), "failed"),
    ],
)
async def test_check_outcomes_are_never_offers(motwi, response, state):
    respx.get(DEFAULT_URL).mock(response)
    view = await motwi.check_update()
    assert view["state"] == state and view["url"] is None
    assert not (await motwi.status())["update"]["available"]
    assert not [w for w in (await motwi.overview())["warnings"] if w["kind"] == "update"]


@respx.mock
async def test_a_failed_check_keeps_an_offer_already_made(motwi):
    route = respx.get(DEFAULT_URL)
    route.mock(httpx.Response(200, json={"tag_name": "v99.0.0", "html_url": "https://x.test/r"}))
    await motwi.check_update()
    route.mock(side_effect=httpx.ConnectError("down"))
    view = await motwi.check_update()
    assert view["state"] == "available" and view["detail"]


async def test_the_check_can_be_turned_off(store):
    m = await Motwi.open(store, secrets=MemorySecrets(), env={"MOTWI_UPDATE_URL": ""})
    assert (await m.check_update())["state"] == "off"
    await m.aclose()


async def test_the_loop_honors_the_setting(motwi, monkeypatch):
    calls = []

    async def fake_check():
        calls.append(1)

    monkeypatch.setattr(motwi, "check_update", fake_check)
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 3:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await motwi.update_loop(sleep=fake_sleep)
    assert len(calls) == 2 and sleeps[0] == 60 and sleeps[1] == 86_400

    await motwi.update_settings({"check_updates": False})
    calls.clear()
    sleeps.clear()
    with pytest.raises(asyncio.CancelledError):
        await motwi.update_loop(sleep=fake_sleep)
    assert calls == []


# start with Windows


class FakeRun:
    def __init__(self):
        self.value = None

    def get(self):
        return self.value

    def set(self, command):
        self.value = command

    def delete(self):
        self.value = None


def test_autostart_registers_the_quoted_exe_in_background_mode():
    run = FakeRun()
    a = AutoStart(run, exe=r"C:\Users\Nagy Péter\Motwi\Motwi.exe")
    assert a.available and not a.enabled
    a.set_enabled(True)
    assert run.value == r'"C:\Users\Nagy Péter\Motwi\Motwi.exe" --background'
    assert a.enabled
    a.set_enabled(False)
    assert run.value is None and not a.enabled


def test_autostart_isnt_available_from_a_dev_checkout():
    a = AutoStart(FakeRun())
    assert not a.available  # pytest isn't a frozen app
    with pytest.raises(ValueError):
        a.set_enabled(True)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows registry")
def test_the_real_registry_round_trips():
    key = rf"Software\MotwiTest-{uuid.uuid4().hex}"
    value = WindowsRunValue(key=key)
    assert value.get() is None
    value.delete()  # deleting nothing is fine
    value.set('"x" --background')
    assert value.get() == '"x" --background'
    value.delete()
    assert value.get() is None
    import winreg

    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)


# API


@pytest.fixture
async def client(motwi):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    quits = []
    run = FakeRun()
    app = create_app(
        motwi,
        auth=ApiAuth("w" * 43),
        port=port,
        on_quit=lambda: quits.append(1),
        autostart=AutoStart(run, exe="C:/Motwi.exe"),
    )
    async with TestClient(TestServer(app, host="127.0.0.1", port=port)) as c:
        c.quits, c.run = quits, run
        yield c


WINDOW = {"Authorization": "Bearer " + "w" * 43}


async def test_quit_needs_the_header_a_web_page_cant_send(client):
    assert (await client.post("/api/quit")).status == 403
    assert client.quits == []
    assert (await client.post("/api/quit", headers={"X-Motwi": "1"})).status == 200
    assert client.quits == [1]


async def test_app_info_and_autostart_are_the_windows_alone(client):
    assert (await client.get("/api/app")).status == 401
    body = await (await client.get("/api/app", headers=WINDOW)).json()
    assert body["version"] == __version__
    assert body["autostart"] == {"available": True, "enabled": False}
    assert body["update"]["state"] == "idle"

    resp = await client.put("/api/app/autostart", json={"enabled": True}, headers=WINDOW)
    assert (await resp.json())["enabled"] is True and "--background" in client.run.value
    bad = await client.put("/api/app/autostart", json={"enabled": "yes"}, headers=WINDOW)
    assert bad.status == 422


async def test_autostart_refusal_reaches_the_field(motwi):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    app = create_app(motwi, auth=ApiAuth("w" * 43), port=port, autostart=AutoStart(FakeRun()))
    async with TestClient(TestServer(app, host="127.0.0.1", port=port)) as c:
        resp = await c.put("/api/app/autostart", json={"enabled": True}, headers=WINDOW)
        assert resp.status == 422
        assert "installed app" in (await resp.json())["error"]


async def test_shutting_down_doesnt_wait_on_open_sockets(motwi):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    app = create_app(motwi, auth=ApiAuth("w" * 43), port=port)
    runner = await serve(app, port)
    async with aiohttp.ClientSession() as session:
        ws = await session.ws_connect(f"http://127.0.0.1:{port}/api/events")
        await ws.send_json({"type": "hello", "token": "w" * 43})
        assert (await ws.receive_json())["type"] == "hello"
        started = time.monotonic()
        await asyncio.wait_for(runner.cleanup(), timeout=5)
        assert time.monotonic() - started < 3
        msg = await ws.receive(timeout=2)
        assert msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED)


# installer jobs


def test_wipe_forgets_credentials_and_only_motwis_files(tmp_path):
    secrets = MemorySecrets()
    tokens = TokenStore(secrets)
    tokens.save("streamer", Token("a", "b", 1.0, (), "1", "me"))
    tokens.save("bot", Token("a", "b", 1.0, (), "2", "bot"))
    secrets.set("ai:primary", "k1")
    secrets.set("ai:backup", "k2")
    home = tmp_path / "Motwi"
    (home / "logs").mkdir(parents=True)
    (home / "webview").mkdir()
    (home / "logs" / "motwi.log").write_text("x")
    (home / "motwi.db").write_text("x")
    (home / "motwi.ico").write_text("x")

    (home / "mine.txt").write_text("keep")
    wipe(secrets, home)
    assert tokens.load("streamer") is None and tokens.load("bot") is None
    assert secrets.get("ai:primary") is None and secrets.get("ai:backup") is None
    assert [p.name for p in home.iterdir()] == ["mine.txt"]

    (home / "mine.txt").unlink()
    wipe(secrets, home)
    assert not home.exists()


def test_self_test_reports_each_check_and_fails_when_one_does(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "motwi.maintenance.CHECKS",
        {"good": lambda: "fine", "bad": lambda: (_ for _ in ()).throw(RuntimeError("nope"))},
    )
    report = tmp_path / "r.json"
    assert self_test(report) is False
    data = json.loads(report.read_text())
    assert data["ok"] is False
    assert data["checks"]["good"] == {"ok": True, "detail": "fine"}
    assert data["checks"]["bad"]["detail"] == "RuntimeError: nope"
