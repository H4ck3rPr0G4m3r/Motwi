"""The only thing the tunnel reaches: a second server with just what a mod may do.

It's a separate app on its own port rather than a filter on the local API, so a mistake in
one route check can't expose settings, quitting or the window's files to the internet.
"""

import asyncio
import io
import json
import logging
import zipfile
from pathlib import Path

from aiohttp import WSMsgType, web

from motwi.api.common import (
    EXTENSION_ORIGIN,
    Handler,
    action_id_of,
    action_json,
    body,
    error,
    guarded,
    ok,
    pump_events,
    text_field,
)
from motwi.api.mods import JOIN_PATH, JoinRefused, ModAccess
from motwi.api.pairing import Pairing
from motwi.service import Motwi
from motwi.store import ClientRecord

log = logging.getLogger(__name__)

HELLO_TIMEOUT_S = 5.0
ZIP_NAME = "motwi-extension.zip"
RECORD = web.AppKey("record", dict)
SOCKETS = web.AppKey("sockets", set)
PUBLIC = ("/mod/hello", "/mod/join", "/mod/events", JOIN_PATH, f"{JOIN_PATH}.js", f"/{ZIP_NAME}")


def _zip_folder(folder: Path) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(folder.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(folder))
    return out.getvalue()


def create_gateway(
    mods: ModAccess, motwi: Motwi, pairing: Pairing, *, ui_dir: Path | None, extension_dir: Path
) -> web.Application:
    def bearer(request: web.Request) -> ClientRecord | None:
        header = request.headers.get("Authorization", "")
        return pairing.mod_for(header[7:] if header.startswith("Bearer ") else None)

    @web.middleware
    async def guard(request: web.Request, handler: Handler) -> web.StreamResponse:
        if request.host not in mods.allowed_hosts():
            return error(403, "forbidden host")
        if request.path not in PUBLIC and bearer(request) is None:
            return error(401, "not signed in to Motwi")
        return await guarded(request, handler)

    @web.middleware
    async def headers(request: web.Request, handler: Handler) -> web.StreamResponse:
        resp = await handler(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("Cache-Control", "no-store")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
        )
        return resp

    app = web.Application(middlewares=[headers, guard], client_max_size=64 * 1024)
    app[SOCKETS] = set()
    zipped: list[bytes] = []

    async def hello(request: web.Request) -> web.Response:
        q = request.query
        return ok(mods.hello(request.host, q.get("client"), q.get("nonce")))

    async def join(request: web.Request) -> web.Response:
        if not request.headers.get("Origin", "").startswith(EXTENSION_ORIGIN):
            return error(403, "forbidden origin")
        data = await body(request)
        try:
            client, token = await mods.join(
                text_field(data, "code"),
                text_field(data, "twitch_token"),
                text_field(data, "label", "Browser"),
            )
        except JoinRefused as e:
            return error(403, str(e))
        return ok(
            {
                "token": token,
                "client_id": client.id,
                "login": client.login,
                "channel": motwi.streamer_login,
            }
        )

    async def chat(request: web.Request) -> web.Response:
        record = bearer(request)
        assert record is not None
        data = await motwi.chat_view()
        data["status"] = motwi.status_for_mods(data["status"])
        data["warnings"] = [w for w in data["warnings"] if w["kind"] in ("twitch", "requests")]
        data["recent"] = [action_json(a) for a in data["recent"]]
        data["open_requests"] = [action_json(a) for a in data["open_requests"]]
        data["present"] = mods.present(viewer=record.login)
        return ok(data)

    async def act(request: web.Request) -> web.Response:
        record = bearer(request)
        assert record is not None
        action_id = action_id_of(request)
        as_warn = (await body(request)).get("as") == "warn"
        action = await motwi.act(
            action_id, request.match_info["verb"], record.login, as_warn=as_warn
        )
        return ok(action_json(action))

    async def leave(request: web.Request) -> web.Response:
        record = bearer(request)
        assert record is not None
        await mods.remove(record.id)
        return ok()

    async def events(request: web.Request) -> web.StreamResponse:
        if not request.headers.get("Origin", "").startswith(EXTENSION_ORIGIN):
            return error(403, "forbidden origin")
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=64 * 1024)
        await ws.prepare(request)
        try:
            first = await ws.receive(timeout=HELLO_TIMEOUT_S)
        except TimeoutError:
            await ws.close(code=4401, message=b"no hello")
            return ws
        token = None
        if first.type == WSMsgType.TEXT:
            try:
                frame = json.loads(first.data)
                token = frame.get("token") if isinstance(frame, dict) else None
            except ValueError:
                token = None
        record = pairing.mod_for(token if isinstance(token, str) else None)
        if record is None:
            await ws.close(code=4401, message=b"not signed in to Motwi")
            return ws
        await pairing.attach(record.id, ws)
        app[SOCKETS].add(ws)
        log.info("event socket opened (mod %s)", record.login)
        mods.check_on_connect(record)

        async def status() -> dict:
            return motwi.status_for_mods(await motwi.status())

        try:
            await pump_events(ws, motwi, status, logins=False)
        finally:
            app[SOCKETS].discard(ws)
            pairing.detach(record.id, ws)
            await ws.close()
        return ws

    async def extension_zip(_: web.Request) -> web.Response:
        if not (extension_dir / "manifest.json").is_file():
            raise KeyError("extension")
        if not zipped:
            zipped.append(await asyncio.to_thread(_zip_folder, extension_dir))
        return web.Response(
            body=zipped[0],
            content_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{ZIP_NAME}"'},
        )

    async def close_sockets(a: web.Application) -> None:
        for ws in list(a[SOCKETS]):
            await ws.close(code=1001, message=b"Motwi is shutting down")

    app.on_shutdown.append(close_sockets)
    app.router.add_get("/mod/hello", hello)
    app.router.add_post("/mod/join", join)
    app.router.add_get("/mod/chat", chat)
    app.router.add_post("/mod/actions/{id}/{verb}", act)
    app.router.add_post("/mod/leave", leave)
    app.router.add_get("/mod/events", events)
    app.router.add_get(f"/{ZIP_NAME}", extension_zip)
    if ui_dir is not None and (ui_dir / "join.html").is_file():
        page, script = ui_dir / "join.html", ui_dir / "join.js"

        async def join_page(_: web.Request) -> web.FileResponse:
            return web.FileResponse(page)

        async def join_script(_: web.Request) -> web.FileResponse:
            return web.FileResponse(script)

        app.router.add_get(JOIN_PATH, join_page)
        app.router.add_get(f"{JOIN_PATH}.js", join_script)
    return app
