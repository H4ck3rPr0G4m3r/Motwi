import asyncio
import json
import socket

import pytest
from aiohttp import WSMsgType, WSServerHandshakeError
from aiohttp.test_utils import TestClient, TestServer
from conftest import make_new_action

from motwi.api import ApiAuth, create_app
from motwi.api.pairing import CODE_TTL_S, Pairing, PairingError
from motwi.events import EventBus
from motwi.models import Kind, Status
from motwi.service import Motwi
from motwi.store import Store
from motwi.twitch.tokens import MemorySecrets

WINDOW = "w" * 43
EXTENSION = {"Origin": "chrome-extension://abcdefghijklmnop"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
async def motwi(monkeypatch):
    monkeypatch.setenv("MOTWI_TWITCH_CLIENT_ID", "cid")
    store = await Store.open(":memory:")
    m = await Motwi.open(store, secrets=MemorySecrets(), env={})
    yield m
    await m.aclose()
    await store.close()


@pytest.fixture
async def client(motwi, tmp_path):
    port = free_port()
    opened: list[str] = []
    revealed: list = []
    ext = tmp_path / "ext"
    ext.mkdir()
    app = create_app(
        motwi,
        auth=ApiAuth(WINDOW),
        port=port,
        open_url=opened.append,
        reveal=revealed.append,
        extension_dir=ext,
    )
    async with TestClient(TestServer(app, host="127.0.0.1", port=port)) as c:
        c.opened, c.revealed, c.ext = opened, revealed, ext
        yield c


def window():
    return {"Authorization": f"Bearer {WINDOW}"}


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


async def start_code(client) -> str:
    resp = await client.post("/api/extension/pair", headers=window())
    assert resp.status == 200
    url = (await resp.json())["url"]
    assert url.startswith("http://127.0.0.1:") and "/pair.html#c=" in url
    return url.split("#c=")[1]


async def pair(client, label="Chrome") -> dict:
    code = await start_code(client)
    resp = await client.post("/api/pair", json={"code": code, "label": label}, headers=EXTENSION)
    assert resp.status == 200
    return await resp.json()


# the pairing object


async def test_a_code_works_once_and_expires():
    store = await Store.open(":memory:")
    now = [1000.0]
    pairing = Pairing(store, EventBus(), clock=lambda: now[0])
    code = pairing.start()
    with pytest.raises(PairingError):
        await pairing.redeem("wrong", "x")
    client, token = await pairing.redeem(code, "Chrome")  # a wrong guess didn't burn it
    assert pairing.check(token) == client.id
    with pytest.raises(PairingError):
        await pairing.redeem(code, "Chrome")

    code = pairing.start()
    now[0] += CODE_TTL_S + 1
    with pytest.raises(PairingError):
        await pairing.redeem(code, "Chrome")
    await store.close()


async def test_a_new_code_replaces_the_old_one():
    store = await Store.open(":memory:")
    pairing = Pairing(store, EventBus())
    old = pairing.start()
    pairing.start()
    with pytest.raises(PairingError):
        await pairing.redeem(old, "x")
    await store.close()


async def test_tokens_are_stored_hashed_and_survive_a_restart():
    store = await Store.open(":memory:")
    pairing = Pairing(store, EventBus())
    client, token = await pairing.redeem(pairing.start(), "  Edge   on  this PC ")
    assert client.label == "Edge on this PC"
    stored = await store.list_clients()
    assert [c.id for c in stored] == [client.id] and token not in stored[0].token_hash

    again = Pairing(store, EventBus())
    assert again.check(token) is None  # nothing loaded yet
    await again.load()
    assert again.check(token) == client.id
    assert again.check("nope") is None and again.check(None) is None
    await store.close()


# the API


async def test_pairing_opens_the_browser_and_only_an_extension_may_redeem(client):
    code = await start_code(client)
    assert client.opened and code in client.opened[0]

    page = await client.post("/api/pair", json={"code": code, "label": "x"})
    assert page.status == 403
    web = await client.post(
        "/api/pair", json={"code": code, "label": "x"}, headers={"Origin": "https://evil.example"}
    )
    assert web.status == 403
    wrong = await client.post("/api/pair", json={"code": "nope", "label": "x"}, headers=EXTENSION)
    assert wrong.status == 410
    assert "Access-Control-Allow-Origin" not in wrong.headers

    good = await client.post("/api/pair", json={"code": code, "label": "x"}, headers=EXTENSION)
    assert good.status == 200 and (await good.json())["token"]
    used = await client.post("/api/pair", json={"code": code, "label": "x"}, headers=EXTENSION)
    assert used.status == 410


async def test_the_extension_token_only_reaches_the_chat_routes(client):
    token = (await pair(client))["token"]
    for method, path in [("GET", "/api/status"), ("GET", "/api/chat")]:
        assert (await client.request(method, path, headers=bearer(token))).status == 200
    assert (
        await client.post("/api/engine", json={"paused": True}, headers=bearer(token))
    ).status == 200
    for method, path in [
        ("GET", "/api/settings"),
        ("PATCH", "/api/settings"),
        ("GET", "/api/ai"),
        ("DELETE", "/api/ai/backup"),
        ("POST", "/api/twitch/logout"),
        ("GET", "/api/rules"),
        ("POST", "/api/rules"),
        ("GET", "/api/extension"),
        ("POST", "/api/extension/pair"),
        ("GET", "/api/actions"),
        ("POST", "/api/actions/1/approve/extra"),
    ]:
        resp = await client.request(method, path, headers=bearer(token))
        assert resp.status == 403, f"{method} {path} gave {resp.status}"


async def test_revoking_ends_the_token_and_closes_its_socket(client):
    paired = await pair(client)
    token = paired["token"]
    ws = await client.ws_connect("/api/events", headers=EXTENSION)
    await ws.send_str(json.dumps({"type": "hello", "token": token}))
    assert (await ws.receive_json())["type"] == "hello"

    listed = await (await client.get("/api/extension", headers=window())).json()
    assert [(c["label"], c["connected"]) for c in listed["clients"]] == [("Chrome", True)]

    resp = await client.delete(f"/api/extension/clients/{paired['client_id']}", headers=window())
    assert resp.status == 200
    msg = await ws.receive(timeout=2)
    while msg.type not in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED):
        msg = await ws.receive(timeout=2)
    assert ws.close_code == 4401
    assert (await client.get("/api/status", headers=bearer(token))).status == 401
    resp = await client.delete(f"/api/extension/clients/{paired['client_id']}", headers=window())
    assert resp.status == 404


async def test_odd_tokens_are_refused_rather_than_crashing(client):
    await pair(client)
    resp = await client.get("/api/status", headers={"Authorization": "Bearer t\u00f6ken"})
    assert resp.status == 401
    code = await start_code(client)
    resp = await client.post(
        "/api/pair", json={"code": "c\u00f6de", "label": "x"}, headers=EXTENSION
    )
    assert resp.status == 410
    assert code

    for hello in ({"token": 123}, {"token": "t\u00f6ken"}, ["token"], "plain"):
        ws = await client.ws_connect("/api/events")
        await ws.send_str(json.dumps(hello))
        await ws.receive()
        assert ws.close_code == 4401, hello


async def test_an_extension_can_remove_itself_but_not_others(client):
    one = await pair(client, "One")
    two = await pair(client, "Two")
    assert (await client.post("/api/extension/leave", headers=window())).status == 404
    assert (await client.post("/api/extension/leave", headers=bearer(one["token"]))).status == 200
    assert (await client.get("/api/status", headers=bearer(one["token"]))).status == 401
    assert (await client.get("/api/status", headers=bearer(two["token"]))).status == 200
    listed = await (await client.get("/api/extension", headers=window())).json()
    assert [c["label"] for c in listed["clients"]] == ["Two"]


async def test_the_socket_refuses_web_pages_and_the_window_token_from_an_extension(client):
    token = (await pair(client))["token"]
    with pytest.raises(WSServerHandshakeError):
        await client.ws_connect("/api/events", headers={"Origin": "https://evil.example"})

    ws = await client.ws_connect("/api/events", headers=EXTENSION)
    await ws.send_str(json.dumps({"type": "hello", "token": WINDOW}))
    assert (await ws.receive()).type in (WSMsgType.CLOSE, WSMsgType.CLOSED)
    assert ws.close_code == 4401

    ws = await client.ws_connect("/api/events", headers=EXTENSION)
    await ws.send_str(json.dumps({"type": "hello", "token": "wrong"}))
    await ws.receive()
    assert ws.close_code == 4401

    ws = await client.ws_connect("/api/events", headers=EXTENSION)
    await ws.send_str(json.dumps({"type": "hello", "token": token}))
    assert (await ws.receive_json())["type"] == "hello"
    await ws.send_str(json.dumps({"type": "ping"}))  # keepalives are ignored, not fatal
    await asyncio.sleep(0.05)
    assert not ws.closed
    await ws.close()


async def test_the_window_hears_when_a_browser_connects(client):
    ws = await client.ws_connect("/api/events")
    await ws.send_str(json.dumps({"type": "hello", "token": WINDOW}))
    assert (await ws.receive_json())["type"] == "hello"
    await pair(client)
    assert (await ws.receive_json(timeout=2))["type"] == "extension"
    await ws.close()


async def test_folder_is_reported_only_when_built(client):
    body = await (await client.get("/api/extension", headers=window())).json()
    assert body == {"clients": [], "folder": None}
    assert (await client.post("/api/extension/reveal", headers=window())).status == 404

    (client.ext / "manifest.json").write_text("{}")
    body = await (await client.get("/api/extension", headers=window())).json()
    assert body["folder"] == str(client.ext)
    assert (await client.post("/api/extension/reveal", headers=window())).status == 200
    assert client.revealed == [client.ext]


async def test_chat_view_counts_what_stands_and_lists_open_requests(client, motwi):
    store = motwi.store
    for kind, status in [
        (Kind.DELETE, Status.DONE),
        (Kind.DELETE, Status.UNDONE),
        (Kind.TIMEOUT, Status.DONE),
        (Kind.TIMEOUT, Status.FAILED),
        (Kind.WARN, Status.SUGGESTED),
        (Kind.BAN_REQUEST, Status.PENDING),
        (Kind.BAN_REQUEST, Status.EXPIRED),
        (Kind.BAN_REQUEST, Status.DENIED),
    ]:
        await store.insert_action(make_new_action(kind=kind, status=status))
    token = (await pair(client))["token"]
    body = await (await client.get("/api/chat", headers=bearer(token))).json()
    assert body["counts"] == {"delete": 1, "warn": 0, "timeout": 1, "ban": 2, "suggested": 1}
    assert [a["status"] for a in body["open_requests"]] == ["expired", "pending"]
    assert len(body["recent"]) == 8
    assert body["status"]["engine"] == "stopped"
    assert any("1 ban request is still waiting" in w["text"] for w in body["warnings"])
