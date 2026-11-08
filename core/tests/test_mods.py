import asyncio
import hashlib
import json
import socket
import subprocess
import sys
import time

import aiohttp
import httpx
import pytest
import respx
from aiohttp.test_utils import TestClient, TestServer
from conftest import make_new_action

from motwi.api import ApiAuth, create_app
from motwi.api.mods import INVITE_TTL_S, RECHECK_S, JoinRefused, proof_for
from motwi.api.server import MODS, PAIRING
from motwi.models import Kind, Status
from motwi.service import Motwi
from motwi.sim.demo import FakeTwitch, LocalTunnel, fake_identify, fake_session
from motwi.store import Store
from motwi.tunnel import Tunnel
from motwi.twitch import BOT_SCOPES, SCOPES
from motwi.twitch.auth import ID_BASE
from motwi.twitch.helix import HELIX, HelixError
from motwi.twitch.tokens import MemorySecrets, Token, TokenStore

WINDOW = "w" * 43
EXT = {"Origin": "chrome-extension://abcdefghijklmnop"}
CHANNEL = "the_streamer"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
async def motwi(monkeypatch):
    monkeypatch.setenv("MOTWI_TWITCH_CLIENT_ID", "cid")
    store = await Store.open(":memory:")
    secrets = MemorySecrets()
    TokenStore(secrets).save("streamer", Token("x", "y", time.time() + 9999, SCOPES, "42", CHANNEL))
    m = await Motwi.open(store, secrets=secrets, env={})
    yield m
    m._session = None
    await m.aclose()
    await store.close()


@pytest.fixture
def twitch(motwi):
    fake = FakeTwitch()
    fake.mods = {"brakkenhoff", "lumenfox"}
    motwi._session = fake_session(motwi, fake, CHANNEL)
    return fake


@pytest.fixture
async def client(motwi, twitch, tmp_path):
    port = free_port()
    ext = tmp_path / "ext"
    ext.mkdir()
    (ext / "manifest.json").write_text("{}", encoding="utf-8")
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text("window", encoding="utf-8")
    (ui / "secret.txt").write_text("window only", encoding="utf-8")
    (ui / "join.html").write_text("join page", encoding="utf-8")
    (ui / "join.js").write_text("// join", encoding="utf-8")
    tunnel = LocalTunnel()
    app = create_app(
        motwi,
        auth=ApiAuth(WINDOW),
        port=port,
        ui_dir=ui,
        extension_dir=ext,
        tunnel=tunnel,
        identify=fake_identify,
    )
    async with TestClient(TestServer(app, host="127.0.0.1", port=port)) as c:
        c.mods, c.pairing, c.tunnel = app[MODS], app[PAIRING], tunnel
        async with aiohttp.ClientSession() as outside:
            c.outside = outside
            yield c


def window():
    return {"Authorization": f"Bearer {WINDOW}"}


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


async def invite(client) -> tuple[str, str]:
    resp = await client.post("/api/mods/invites", headers=window())
    assert resp.status == 201
    url = (await resp.json())["url"]
    base, code = url.split("/motwi-join#c=")
    return base, code


async def join(client, base, code, login="brakkenhoff", label="Chrome"):
    return await client.outside.post(
        f"{base}/mod/join",
        json={"code": code, "twitch_token": f"demo-{login}", "label": label},
        headers=EXT,
    )


async def joined(client, login="brakkenhoff") -> tuple[str, str]:
    base, code = await invite(client)
    resp = await join(client, base, code, login)
    assert resp.status == 200, await resp.text()
    return base, (await resp.json())["token"]


async def pending_ban(motwi) -> int:
    record = await motwi.store.insert_action(
        make_new_action(kind=Kind.BAN_REQUEST, status=Status.PENDING, hold_applied=True)
    )
    return record.id


# joining


async def test_nothing_runs_until_it_is_needed(client):
    assert client.tunnel.state == "off"
    view = await (await client.get("/api/mods", headers=window())).json()
    assert view["link"] is None and view["mods"] == [] and view["can_invite"] is True


async def test_a_real_mod_joins_with_an_invite(client):
    base, code = await invite(client)
    resp = await join(client, base, code)
    assert resp.status == 200
    data = await resp.json()
    assert data["login"] == "brakkenhoff" and data["channel"] == CHANNEL
    view = await (await client.get("/api/mods", headers=window())).json()
    assert [m["login"] for m in view["mods"]] == ["brakkenhoff"]
    assert view["invites"] == []


async def test_an_invite_works_once(client):
    base, code = await invite(client)
    assert (await join(client, base, code)).status == 200
    resp = await join(client, base, code, "lumenfox")
    assert resp.status == 403
    assert "already used" in (await resp.json())["error"]


async def test_a_leaked_invite_is_useless_and_still_works_for_the_real_mod(client):
    base, code = await invite(client)
    resp = await join(client, base, code, "some_stranger")
    assert resp.status == 403
    assert f"isn't a moderator of {CHANNEL}" in (await resp.json())["error"]
    assert (await join(client, base, code, "brakkenhoff")).status == 200


async def test_a_made_up_code_never_reaches_twitch(client, motwi):
    calls = []

    async def spy(token):
        calls.append(token)
        return await fake_identify(token)

    client.mods._identify = spy
    base, _ = await invite(client)
    assert (await join(client, base, "nope")).status == 403
    assert calls == []


async def test_an_invite_expires(client):
    base, code = await invite(client)
    real = client.mods._clock
    client.mods._clock = lambda: real() + INVITE_TTL_S + 1
    assert (await join(client, base, code)).status == 403


async def test_a_web_page_cant_join(client):
    base, code = await invite(client)
    resp = await client.outside.post(
        f"{base}/mod/join",
        json={"code": code, "twitch_token": "demo-brakkenhoff"},
        headers={"Origin": "https://evil.example"},
    )
    assert resp.status == 403


async def test_joining_again_from_the_same_browser_replaces_the_old_entry(client):
    base, first = await joined(client)
    base, second = await joined(client)
    assert first != second
    assert len(await client.pairing.clients("mod")) == 1
    assert (await client.outside.get(f"{base}/mod/chat", headers=bearer(first))).status == 401


async def test_inviting_needs_the_scope_an_old_sign_in_lacks(client, motwi):
    old = tuple(s for s in SCOPES if s != "moderation:read")
    motwi._tokens.save("streamer", Token("x", "y", time.time() + 9999, old, "42", CHANNEL))
    resp = await client.post("/api/mods/invites", headers=window())
    assert resp.status == 422
    view = await (await client.get("/api/mods", headers=window())).json()
    assert view["can_invite"] is False


def test_only_the_streamers_sign_in_asks_to_read_moderators():
    assert "moderation:read" in SCOPES
    assert "moderation:read" not in BOT_SCOPES


# the identity check


@respx.mock
async def test_a_twitch_token_from_another_app_is_refused(client):
    client.mods._identify = client.mods._identify_with_twitch
    respx.get(f"{ID_BASE}/validate").mock(
        return_value=httpx.Response(200, json={"client_id": "other", "user_id": "1", "login": "x"})
    )
    with pytest.raises(JoinRefused, match="didn't come from Motwi"):
        await client.mods._identify("token")


@respx.mock
async def test_a_bad_twitch_token_is_refused(client):
    respx.get(f"{ID_BASE}/validate").mock(return_value=httpx.Response(401, json={}))
    with pytest.raises(JoinRefused, match="didn't accept"):
        await client.mods._identify_with_twitch("token")


@respx.mock
async def test_helix_asks_about_exactly_that_user(motwi):
    route = respx.get(f"{HELIX}/moderation/moderators").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    motwi._session = None
    assert await motwi.is_channel_mod("7") is False
    assert route.calls.last.request.url.params["user_id"] == "7"
    assert route.calls.last.request.url.params["broadcaster_id"] == "42"
    route.mock(return_value=httpx.Response(200, json={"data": [{"user_id": "7"}]}))
    assert await motwi.is_channel_mod("7") is True


async def test_the_streamer_counts_as_a_mod_of_their_own_channel(motwi):
    assert await motwi.is_channel_mod("42") is True


# what a mod can do


async def test_a_mod_token_only_opens_the_gateway(client):
    base, token = await joined(client)
    assert (await client.outside.get(f"{base}/mod/chat", headers=bearer(token))).status == 200
    assert (await client.get("/api/status", headers=bearer(token))).status == 401
    assert (await client.get("/api/settings", headers=bearer(token))).status == 401
    assert (await client.post("/api/engine", headers=bearer(token), json={})).status == 401


async def test_a_streamer_browser_token_doesnt_open_the_gateway(client):
    base, _ = await joined(client)
    _, token = await client.pairing.redeem(client.pairing.start(), "Chrome")
    assert (await client.outside.get(f"{base}/mod/chat", headers=bearer(token))).status == 401


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/status"),
        ("GET", "/api/settings"),
        ("POST", "/api/quit"),
        ("POST", "/api/show"),
        ("GET", "/mod/settings"),
        ("POST", "/mod/engine"),
        ("POST", "/mod/rules"),
        ("GET", "/secret.txt"),
        ("GET", "/"),
    ],
)
async def test_the_gateway_has_nothing_else(client, method, path):
    base, token = await joined(client)
    resp = await client.outside.request(
        method, f"{base}{path}", headers={**bearer(token), "X-Motwi": "1"}
    )
    assert resp.status == 404


async def test_the_gateway_refuses_other_hosts(client):
    base, token = await joined(client)
    resp = await client.outside.get(
        f"{base}/mod/chat", headers={**bearer(token), "Host": "evil.example"}
    )
    assert resp.status == 403


async def test_a_mod_cant_call_without_a_token(client):
    base, _ = await joined(client)
    assert (await client.outside.get(f"{base}/mod/chat")).status == 401
    resp = await client.outside.post(f"{base}/mod/actions/1/approve", json={})
    assert resp.status == 401


async def test_a_ban_is_credited_to_the_mod_and_named_to_twitch(client, motwi, twitch):
    reasons = []
    real = twitch.ban

    async def ban(user_id, reason):
        reasons.append(reason)
        await real(user_id, reason)

    twitch.ban = ban
    base, token = await joined(client)
    action = await pending_ban(motwi)
    resp = await client.outside.post(
        f"{base}/mod/actions/{action}/approve", json={}, headers=bearer(token)
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "done" and data["actor"] == "brakkenhoff"
    assert reasons and "brakkenhoff" in reasons[0]


async def test_first_answer_wins_between_a_mod_and_the_streamer(client, motwi):
    base, token = await joined(client)
    action = await pending_ban(motwi)
    first = await client.outside.post(
        f"{base}/mod/actions/{action}/deny", json={}, headers=bearer(token)
    )
    assert first.status == 200
    second = await client.post(f"/api/actions/{action}/approve", json={}, headers=window())
    assert second.status == 409


async def test_a_mod_sees_no_costs_keys_or_updates(client, motwi):
    base, token = await joined(client)
    status = (await (await client.outside.get(f"{base}/mod/chat", headers=bearer(token))).json())[
        "status"
    ]
    assert status["month_cost"] == 0
    assert status["accounts"]["bot"] is None
    assert status["update"] == {"available": False, "version": None, "url": None}
    for view in (status["ai"]["primary"], status["ai"]["backup"]):
        assert view is None or set(view) == {"provider", "model"}


# staying a mod


async def test_a_demoted_mod_loses_access_and_their_socket(client, twitch):
    base, token = await joined(client)
    ws = await client.outside.ws_connect(f"{base}/mod/events", headers=EXT)
    await ws.send_json({"type": "hello", "token": token})
    assert (await ws.receive_json())["type"] == "hello"
    await client.mods.settled()

    twitch.mods.discard("brakkenhoff")
    await client.mods.recheck()
    await client.mods.settled()

    frame = await ws.receive(timeout=2)
    assert (frame.type, frame.data) == (aiohttp.WSMsgType.CLOSE, 4401)
    assert client.pairing.mod_for(token) is None


async def test_a_twitch_outage_removes_nobody(client, twitch):
    await joined(client)

    async def down(*_):
        raise HelixError(0, "could not reach Twitch")

    twitch.is_moderator = down
    await client.mods.recheck()
    await client.mods.settled()
    view = await (await client.get("/api/mods", headers=window())).json()
    assert [m["login"] for m in view["mods"]] == ["brakkenhoff"]
    assert view["check_problem"]


async def test_the_check_runs_on_a_schedule(client, twitch):
    await joined(client)
    twitch.mods.clear()
    client.mods._next_check = 0
    await client.mods._tick()
    await client.mods.settled()
    assert await client.pairing.clients("mod") == []
    # and not again before it's due
    await joined_again(client, twitch)
    twitch.mods.clear()
    client.mods._next_check = client.mods._clock() + RECHECK_S
    await client.mods._tick()
    assert len(await client.pairing.clients("mod")) == 1


async def joined_again(client, twitch):
    twitch.mods.add("brakkenhoff")
    await joined(client)


async def test_connecting_rechecks_right_away(client, twitch):
    base, token = await joined(client)
    twitch.mods.clear()
    ws = await client.outside.ws_connect(f"{base}/mod/events", headers=EXT)
    await ws.send_json({"type": "hello", "token": token})
    await ws.receive()
    await client.mods.settled()
    assert await client.pairing.clients("mod") == []
    await ws.close()


# the address


async def test_hello_proves_motwi_to_a_paired_mod_without_a_secret_leaving(client):
    base, token = await joined(client)
    mod = (await client.pairing.clients("mod"))[0]
    hello = await (
        await client.outside.get(f"{base}/mod/hello", params={"client": mod["id"], "nonce": "n1"})
    ).json()
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    assert hello["channel"] == CHANNEL and hello["known"] is True
    assert hello["proof"] == proof_for(token_hash, base, "n1")
    assert token not in json.dumps(hello) and token_hash not in json.dumps(hello)


async def test_hello_says_nothing_about_strangers(client):
    base, _ = await joined(client)
    hello = await (
        await client.outside.get(f"{base}/mod/hello", params={"client": "nobody", "nonce": "n1"})
    ).json()
    assert hello["known"] is False and "proof" not in hello


def test_the_proof_matches_the_extensions():
    # the same value is pinned in extension/test/logic.test.ts
    token_hash = hashlib.sha256(b"mod-token").hexdigest()
    assert (
        proof_for(token_hash, "https://a.trycloudflare.com", "n1")
        == "3dc8a57f7b34e8df3469e089285f0485f62aa05eda219ba9e0da2e3ed358dafb"
    )


async def test_a_proof_is_bound_to_the_address(client):
    assert proof_for("h", "https://a.example", "n") != proof_for("h", "https://b.example", "n")
    assert proof_for("h", "https://a.example", "n") != proof_for("h", "https://a.example", "m")


async def test_a_new_address_doesnt_need_a_new_token(client, motwi):
    base, token = await joined(client)
    mods = client.mods
    await mods._stop_serving()
    await mods._start_serving()
    moved = client.tunnel.url
    assert moved != base
    assert (await client.outside.get(f"{moved}/mod/chat", headers=bearer(token))).status == 200


async def move(client) -> str:
    await client.mods._stop_serving()
    await client.mods._start_serving()
    return client.tunnel.url


async def test_a_mod_who_just_joined_doesnt_need_a_new_link(client):
    await joined(client)
    assert await client.mods.warnings() == []


async def test_the_streamer_is_told_to_share_a_new_address_until_they_do(client):
    await joined(client)
    await move(client)
    assert any(w["kind"] == "mods" for w in await client.mods.warnings())
    view = await (await client.get("/api/mods", headers=window())).json()
    assert view["needs_sharing"] is True
    assert (await client.post("/api/mods/link/shared", headers=window())).status == 200
    assert await client.mods.warnings() == []


async def test_no_nag_when_every_mod_is_already_connected(client):
    _, token = await joined(client)
    base = await move(client)
    ws = await client.outside.ws_connect(f"{base}/mod/events", headers=EXT)
    await ws.send_json({"type": "hello", "token": token})
    await ws.receive_json()
    assert await client.mods.warnings() == []
    await ws.close()


async def test_the_tunnel_runs_only_while_someone_could_use_it(client):
    base, token = await joined(client)
    assert client.tunnel.state == "up"
    resp = await client.outside.post(f"{base}/mod/leave", headers=bearer(token))
    assert resp.status == 200
    await client.mods.settled()
    assert client.tunnel.state == "off"


async def test_a_cancelled_invite_stops_the_tunnel_too(client):
    await invite(client)
    view = await (await client.get("/api/mods", headers=window())).json()
    resp = await client.delete(f"/api/mods/invites/{view['invites'][0]['id']}", headers=window())
    assert resp.status == 200
    assert client.tunnel.state == "off"


async def test_a_paired_mod_brings_the_tunnel_back_on_the_next_start(motwi, twitch, tmp_path):
    await motwi.store.add_client("mod", "Chrome", "hash", "id-brakkenhoff", "brakkenhoff")
    tunnel = LocalTunnel()
    port = free_port()
    app = create_app(motwi, auth=ApiAuth(WINDOW), port=port, tunnel=tunnel, identify=fake_identify)
    async with TestClient(TestServer(app, host="127.0.0.1", port=port)):
        assert tunnel.state == "up"
    assert tunnel.state == "off"


# who's looking


async def test_each_side_sees_who_else_is_there(client):
    base, token = await joined(client)
    ws = await client.outside.ws_connect(f"{base}/mod/events", headers=EXT)
    await ws.send_json({"type": "hello", "token": token})
    await ws.receive_json()
    own = await (await client.get("/api/chat", headers=window())).json()
    assert own["present"] == ["brakkenhoff"]
    theirs = await (await client.outside.get(f"{base}/mod/chat", headers=bearer(token))).json()
    assert theirs["present"] == []
    await ws.close()


# getting the extension


async def test_the_gateway_serves_the_join_page_and_the_extension(client):
    base, _ = await invite(client)
    assert await (await client.outside.get(f"{base}/motwi-join")).text() == "join page"
    zipped = await client.outside.get(f"{base}/motwi-extension.zip")
    assert zipped.status == 200 and zipped.content_type == "application/zip"
    assert (await zipped.read())[:2] == b"PK"


# the tunnel itself


class FakeProcess:
    def __init__(self, lines):
        self.pid = 1
        self.returncode = None
        self.stderr = asyncio.StreamReader()
        for line in lines:
            self.stderr.feed_data(line.encode() + b"\n")
        self.ended = asyncio.Event()

    def finish(self):
        self.stderr.feed_eof()
        self.returncode = 1
        self.ended.set()

    async def wait(self):
        await self.ended.wait()

    def terminate(self):
        self.finish()

    def kill(self):
        self.finish()


@pytest.fixture
def spawned(monkeypatch):
    processes: list[FakeProcess] = []
    scripts: list[list[str]] = []

    async def spawn(*_args, **_kwargs):
        process = FakeProcess(scripts.pop(0) if scripts else [])
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr("motwi.tunnel._die_with_us", lambda _pid: None)
    monkeypatch.setattr("motwi.tunnel.RETRY_DELAYS_S", (0.01,))
    return processes, scripts


UP = [
    "INF Requesting new quick Tunnel on trycloudflare.com...",
    "INF |  https://our-moms-burke-shadow.trycloudflare.com    |",
    "INF Registered tunnel connection connIndex=0",
]


async def resolved(_url):
    return True


async def until(condition):
    for _ in range(200):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("never happened")


async def test_the_address_counts_only_once_a_connection_is_registered(spawned, tmp_path):
    processes, scripts = spawned
    exe = tmp_path / "cloudflared.exe"
    exe.touch()
    scripts.append(UP[:2])
    tunnel = Tunnel(exe, tmp_path / "c.yml", resolves=resolved)
    await tunnel.start(1234)
    await until(lambda: processes)
    await asyncio.sleep(0.05)
    assert tunnel.state == "starting" and tunnel.url is None
    processes[0].stderr.feed_data((UP[2] + "\n").encode())
    await until(lambda: tunnel.state == "up")
    assert tunnel.url == "https://our-moms-burke-shadow.trycloudflare.com"
    await tunnel.stop()
    assert tunnel.state == "off" and tunnel.url is None


async def test_api_trycloudflare_in_an_error_isnt_taken_for_the_address(spawned, tmp_path):
    processes, scripts = spawned
    exe = tmp_path / "cloudflared.exe"
    exe.touch()
    scripts.append(
        [
            'ERR failed to request: Post "https://api.trycloudflare.com/tunnel": 429',
            "INF Registered tunnel connection connIndex=0",
        ]
    )
    tunnel = Tunnel(exe, tmp_path / "c.yml", resolves=resolved)
    await tunnel.start(1234)
    await until(lambda: processes)
    await asyncio.sleep(0.05)
    assert tunnel.url is None and tunnel.state != "up"
    await tunnel.stop()


async def test_a_crash_brings_up_a_new_address(spawned, tmp_path):
    processes, scripts = spawned
    exe = tmp_path / "cloudflared.exe"
    exe.touch()
    second = [UP[0], "INF |  https://a-new-one.trycloudflare.com |", UP[2]]
    scripts.extend([UP, second])
    changes = []
    tunnel = Tunnel(
        exe, tmp_path / "c.yml", on_change=lambda: changes.append(tunnel.url), resolves=resolved
    )
    await tunnel.start(1234)
    await until(lambda: tunnel.state == "up")
    processes[0].finish()
    await until(lambda: tunnel.url == "https://a-new-one.trycloudflare.com")
    assert None in changes  # the old address was withdrawn in between
    await tunnel.stop()


async def test_the_address_is_held_back_until_it_resolves(spawned, tmp_path, monkeypatch):
    # a lookup made too early is remembered as "no such site", so a mod could be locked out
    monkeypatch.setattr("motwi.tunnel.DNS_POLL_S", 0.01)
    _, scripts = spawned
    exe = tmp_path / "cloudflared.exe"
    exe.touch()
    scripts.append(UP)
    answers = [False, False, True]
    asked = []

    async def resolves(url):
        asked.append(url)
        return answers.pop(0)

    tunnel = Tunnel(exe, tmp_path / "c.yml", resolves=resolves)
    await tunnel.start(1234)
    await until(lambda: len(asked) == 1)
    assert tunnel.url is None and tunnel.state == "starting"
    await until(lambda: tunnel.state == "up")
    assert len(asked) == 3
    assert tunnel.url == "https://our-moms-burke-shadow.trycloudflare.com"
    await tunnel.stop()


async def test_a_blocked_dns_check_doesnt_hold_the_address_forever(spawned, tmp_path, monkeypatch):
    monkeypatch.setattr("motwi.tunnel.DNS_POLL_S", 0.01)
    monkeypatch.setattr("motwi.tunnel.DNS_WAIT_S", 0.05)
    _, scripts = spawned
    exe = tmp_path / "cloudflared.exe"
    exe.touch()
    scripts.append(UP)

    async def blocked(_url):
        raise httpx.ConnectError("blocked")

    tunnel = Tunnel(exe, tmp_path / "c.yml", resolves=blocked)
    await tunnel.start(1234)
    await until(lambda: tunnel.state == "up")
    await tunnel.stop()


async def test_a_missing_cloudflared_says_so(tmp_path):
    tunnel = Tunnel(tmp_path / "nope.exe", tmp_path / "c.yml")
    await tunnel.start(1234)
    await until(lambda: tunnel.state == "failed")
    assert "missing" in tunnel.detail
    await tunnel.stop()


@pytest.mark.skipif(sys.platform != "win32", reason="job objects are a Windows thing")
def test_the_child_dies_with_us_even_on_a_hard_kill():
    script = (
        "import subprocess, sys, time\n"
        "from motwi.tunnel import _die_with_us\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "job = _die_with_us(child.pid)\n"
        "print(child.pid if job else 0, flush=True)\n"
        "time.sleep(60)\n"
    )
    parent = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    child_pid = int(parent.stdout.readline())
    assert child_pid
    assert _alive(child_pid)
    parent.kill()
    parent.wait()
    deadline = time.monotonic() + 5
    while _alive(child_pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not _alive(child_pid)


def _alive(pid: int) -> bool:
    out = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, check=False
    ).stdout
    return str(pid) in out
