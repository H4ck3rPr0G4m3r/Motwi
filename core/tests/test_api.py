import asyncio
import json
import socket

import pytest
from aiohttp import WSMsgType, WSServerHandshakeError
from aiohttp.test_utils import TestClient, TestServer
from conftest import make_new_action

from motwi.api import ApiAuth, create_app
from motwi.events import LoginFinished
from motwi.models import Kind, Status
from motwi.service import Motwi
from motwi.store import Store
from motwi.twitch.tokens import MemorySecrets

TOKEN = "t" * 43


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
    (tmp_path / "index.html").write_text("<!doctype html><title>Motwi</title>")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    port = free_port()
    shown = []
    app = create_app(
        motwi, auth=ApiAuth(TOKEN), port=port, ui_dir=tmp_path, on_show=lambda: shown.append(1)
    )
    async with TestClient(TestServer(app, host="127.0.0.1", port=port)) as c:
        c.shown = shown

        yield c


def auth(**extra):
    return {"Authorization": f"Bearer {TOKEN}", **extra}


async def test_api_requires_the_token(client):
    assert (await client.get("/api/status")).status == 401
    bad = await client.get("/api/status", headers={"Authorization": "Bearer nope"})
    assert bad.status == 401
    good = await client.get("/api/status", headers=auth())
    assert good.status == 200
    assert (await good.json())["twitch"]["state"] == "signed_out"


async def test_foreign_host_is_refused(client):
    resp = await client.get("/api/ping", headers={"Host": "evil.example:47815"})
    assert resp.status == 403
    resp = await client.get("/", headers={"Host": f"localhost:{client.server.port}"})
    assert resp.status == 200


async def test_ping_needs_no_token_and_show_needs_the_header(client):
    assert (await (await client.get("/api/ping")).json())["app"] == "motwi"
    assert (await client.post("/api/show")).status == 403
    assert (await client.post("/api/show", headers={"X-Motwi": "1"})).status == 200
    assert client.shown == [1]


async def test_ui_files_are_served_with_a_strict_policy(client):
    resp = await client.get("/")
    assert resp.status == 200 and "Motwi" in await resp.text()
    assert "default-src 'self'" in resp.headers["Content-Security-Policy"]
    assert "Access-Control-Allow-Origin" not in resp.headers
    assert (await client.get("/assets/app.js")).status == 200


async def test_field_errors_name_the_field(client):
    resp = await client.patch("/api/settings", json={"hold_timeout_s": 1}, headers=auth())
    assert resp.status == 422
    body = await resp.json()
    assert body["field"] == "hold_timeout_s" and body["error"]


async def test_bad_json_is_a_400(client):
    resp = await client.patch("/api/settings", data="{nope", headers=auth())
    assert resp.status == 400


async def test_rules_round_trip(client):
    resp = await client.post(
        "/api/rules",
        json={"text": "No spoilers", "ceiling": "delete", "scope": "stream"},
        headers=auth(),
    )
    assert resp.status == 201
    rule = await resp.json()
    assert rule["ceiling"] == "delete"
    rules = await (await client.get("/api/rules", headers=auth())).json()
    assert rule in rules
    patched = await client.patch(
        f"/api/rules/{rule['id']}", json={"ceiling": "warn"}, headers=auth()
    )
    assert (await patched.json())["ceiling"] == "warn"
    assert (await client.delete(f"/api/rules/{rule['id']}", headers=auth())).status == 200
    assert (await client.patch("/api/rules/zzz", json={}, headers=auth())).status == 404


async def test_history_filters_and_serializes_actions(client, motwi):
    await motwi.store.insert_action(make_new_action(kind=Kind.TIMEOUT, duration_s=60))
    await motwi.store.insert_action(make_new_action(kind=Kind.WARN, status=Status.UNDONE))
    resp = await client.get("/api/actions?kind=timeout", headers=auth())
    [a] = await resp.json()
    assert (a["kind"], a["status"], a["duration_s"]) == ("timeout", "done", 60)
    assert (await client.get("/api/actions?kind=nuke", headers=auth())).status == 422


async def test_actions_without_twitch_explain(client):
    resp = await client.post("/api/actions/1/undo", headers=auth())
    assert resp.status == 409
    assert "isn't connected" in (await resp.json())["error"]
    assert (await client.post("/api/actions/1/explode", headers=auth())).status == 404


async def test_websocket_needs_hello_then_streams_events(client, motwi):
    ws = await client.ws_connect("/api/events")
    await ws.send_str(json.dumps({"type": "hello", "token": TOKEN}))
    hello = await ws.receive_json(timeout=2)
    assert hello["type"] == "hello" and "engine" in hello["status"]
    motwi.bus.publish(LoginFinished("streamer", False, None, "denied"))
    msg = await ws.receive_json(timeout=2)
    assert msg == {
        "type": "login",
        "role": "streamer",
        "ok": False,
        "login": None,
        "error": "denied",
    }
    await ws.close()


async def test_websocket_with_a_wrong_token_is_closed(client):
    ws = await client.ws_connect("/api/events")
    await ws.send_str(json.dumps({"type": "hello", "token": "nope"}))
    msg = await ws.receive(timeout=2)
    assert msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSED)
    assert ws.close_code == 4401


async def test_websocket_from_a_web_page_is_refused(client):
    with pytest.raises(WSServerHandshakeError):
        await client.ws_connect("/api/events", headers={"Origin": "https://evil.example"})


async def test_status_changes_coalesce_into_one_snapshot(client, motwi):
    ws = await client.ws_connect("/api/events")
    await ws.send_str(json.dumps({"type": "hello", "token": TOKEN}))
    await ws.receive_json(timeout=2)
    await motwi.update_settings({"strictness": "strict"})
    await motwi.update_settings({"suggest_only": True})
    msg = await ws.receive_json(timeout=2)
    assert msg["type"] == "status" and msg["status"]["suggest_only"] is True
    with pytest.raises(asyncio.TimeoutError):
        await ws.receive_json(timeout=0.5)
    await ws.close()
