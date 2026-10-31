"""aiohttp application for the local API and the window's UI files.

Security model (spec 2, anti-goals):
- It listens on 127.0.0.1 only, and a request whose Host isn't this server is refused,
  which stops DNS rebinding.
- Every /api call carries a bearer token. The WebSocket sends its token in the first
  frame, because browsers can't set headers on a WebSocket.
- There are no CORS headers, so no web page can read a response. The extension's host
  permissions let it read them without any.
- A paired extension has its own token, limited to what the in-chat bar needs.
- Mods never reach this server. The tunnel points at a separate gateway (api/gateway.py).
"""

import asyncio
import hmac
import json
import logging
import os
import re
import secrets
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web

from motwi import __version__
from motwi.api.common import (
    EXTENSION_ORIGIN,
    Handler,
    action_id_of,
    action_json,
    guarded,
    ok,
    pump_events,
)
from motwi.api.common import body as _body
from motwi.api.common import error as _error
from motwi.api.common import text_field as _str
from motwi.api.gateway import create_gateway
from motwi.api.mods import Identify, ModAccess
from motwi.api.pairing import CODE_TTL_S, Pairing, PairingError
from motwi.autostart import AutoStart
from motwi.models import Kind, Source, Status
from motwi.paths import cloudflared_exe, data_dir
from motwi.paths import extension_dir as default_extension_dir
from motwi.service import FieldError, Motwi
from motwi.store import ActionFilter
from motwi.tunnel import Tunnel, TunnelLike

log = logging.getLogger(__name__)

DEFAULT_PORT = 47815
HELLO_TIMEOUT_S = 5.0

PUBLIC_PATHS = ("/api/ping", "/api/show", "/api/quit", "/api/events", "/api/pair")
# everything a paired extension may call; the rest of the API is the window's alone
EXTENSION_ROUTES = (
    ("GET", re.compile(r"/api/status")),
    ("GET", re.compile(r"/api/chat")),
    ("POST", re.compile(r"/api/engine")),
    ("POST", re.compile(r"/api/actions/\d+/\w+")),
    ("POST", re.compile(r"/api/extension/leave")),
)


class ApiAuth:
    """The tokens that may use the API. The window's token lives only for this launch."""

    def __init__(self, window_token: str | None = None) -> None:
        self.window_token = window_token or secrets.token_urlsafe(32)

    def check(self, token: str | None) -> str | None:
        """The client kind for a valid token, else None."""
        if token and hmac.compare_digest(token.encode(), self.window_token.encode()):
            return "window"
        return None


MOTWI = web.AppKey("motwi", Motwi)
AUTH = web.AppKey("auth", ApiAuth)
PAIRING = web.AppKey("pairing", Pairing)
MODS = web.AppKey("mods", ModAccess)
SOCKETS = web.AppKey("sockets", set)
ORIGINS = web.AppKey("origins", set)


# middleware


def _allowed_hosts(port: int) -> set[str]:
    return {f"127.0.0.1:{port}", f"localhost:{port}"}


def _middlewares(port: int) -> list:
    hosts = _allowed_hosts(port)

    @web.middleware
    async def guard(request: web.Request, handler: Handler) -> web.StreamResponse:
        if request.host not in hosts:
            return _error(403, "forbidden host")
        path = request.path
        if path.startswith("/api/") and path not in PUBLIC_PATHS:
            header = request.headers.get("Authorization", "")
            token = header[7:] if header.startswith("Bearer ") else None
            if request.app[AUTH].check(token) is None:
                if request.app[PAIRING].check(token) is None:
                    return _error(401, "not signed in to Motwi")
                if not _extension_may(request.method, path):
                    return _error(403, "the extension can't do that")
        return await guarded(request, handler)

    @web.middleware
    async def headers(request: web.Request, handler: Handler) -> web.StreamResponse:
        resp = await handler(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("Cache-Control", "no-store")
        if not request.path.startswith("/api/"):
            resp.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                f"connect-src 'self' ws://127.0.0.1:{port} ws://localhost:{port}; "
                "frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
            )
        return resp

    return [headers, guard]


def _extension_may(method: str, path: str) -> bool:
    return any(method == m and pattern.fullmatch(path) for m, pattern in EXTENSION_ROUTES)


def _role(value: Any) -> str:
    if value not in ("streamer", "bot"):
        raise FieldError("role", "Unknown account.")
    return value


# routes


def _reveal_folder(path: Path) -> None:
    if hasattr(os, "startfile"):
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        webbrowser.open(path.as_uri())


def _routes(
    motwi: Motwi,
    pairing: Pairing,
    mods: ModAccess,
    *,
    port: int,
    on_show: Callable[[], None] | None,
    on_quit: Callable[[], None] | None,
    autostart: AutoStart,
    open_url: Callable[[str], Any],
    reveal: Callable[[Path], Any],
    extension_dir: Path,
) -> web.RouteTableDef:
    r = web.RouteTableDef()

    @r.get("/api/ping")
    async def ping(_: web.Request) -> web.Response:
        return ok({"app": "motwi", "version": __version__})

    @r.post("/api/show")
    async def show(request: web.Request) -> web.Response:
        # A web page can't send a custom header cross-origin without a CORS preflight,
        # which this server never grants.
        if request.headers.get("X-Motwi") != "1":
            return _error(403, "forbidden")
        if on_show is not None:
            on_show()
        return ok()

    @r.post("/api/quit")
    async def quit_app(request: web.Request) -> web.Response:
        # the installer asking a running Motwi to stop; same guard as /api/show
        if request.headers.get("X-Motwi") != "1":
            return _error(403, "forbidden")
        if on_quit is None:
            return _error(409, "this Motwi can't be stopped from here")
        on_quit()
        return ok()

    @r.get("/api/app")
    async def app_info(_: web.Request) -> web.Response:
        return ok(
            {
                "version": __version__,
                "autostart": {"available": autostart.available, "enabled": autostart.enabled},
                "update": motwi.update_view(),
            }
        )

    @r.put("/api/app/autostart")
    async def set_autostart(request: web.Request) -> web.Response:
        enabled = (await _body(request)).get("enabled")
        if not isinstance(enabled, bool):
            raise FieldError("enabled", "enabled must be true or false.")
        try:
            autostart.set_enabled(enabled)
        except (ValueError, OSError) as e:
            raise FieldError("enabled", str(e)) from e
        return ok({"available": autostart.available, "enabled": autostart.enabled})

    @r.post("/api/app/check-update")
    async def check_update(_: web.Request) -> web.Response:
        return ok(await motwi.check_update())

    @r.get("/api/status")
    async def status(_: web.Request) -> web.Response:
        return ok(await motwi.status())

    @r.post("/api/engine")
    async def engine(request: web.Request) -> web.Response:
        data = await _body(request)
        paused = data.get("paused")
        if not isinstance(paused, bool):
            raise FieldError("paused", "paused must be true or false.")
        await motwi.set_paused(paused)
        return ok(await motwi.status())

    @r.get("/api/settings")
    async def get_settings(_: web.Request) -> web.Response:
        return ok(await motwi.settings())

    @r.patch("/api/settings")
    async def patch_settings(request: web.Request) -> web.Response:
        return ok(await motwi.update_settings(await _body(request)))

    # rules

    @r.get("/api/rules")
    async def rules(_: web.Request) -> web.Response:
        return ok(await motwi.store.list_rules())

    @r.post("/api/rules")
    async def add_rule(request: web.Request) -> web.Response:
        data = await _body(request)
        rule = await motwi.add_rule(
            _str(data, "text"), _str(data, "ceiling", "timeout"), _str(data, "scope", "community")
        )
        return ok(rule, status=201)

    @r.patch("/api/rules/{id}")
    async def update_rule(request: web.Request) -> web.Response:
        return ok(await motwi.update_rule(request.match_info["id"], await _body(request)))

    @r.delete("/api/rules/{id}")
    async def delete_rule(request: web.Request) -> web.Response:
        await motwi.delete_rule(request.match_info["id"])
        return ok()

    # blocked words

    @r.get("/api/blocked")
    async def blocked(_: web.Request) -> web.Response:
        return ok(await motwi.store.list_blocked())

    @r.post("/api/blocked")
    async def add_blocked(request: web.Request) -> web.Response:
        data = await _body(request)
        word = await motwi.add_blocked(
            _str(data, "phrase"), _str(data, "ceiling", "timeout"), bool(data.get("hidden"))
        )
        return ok(word, status=201)

    @r.delete("/api/blocked/{id}")
    async def delete_blocked(request: web.Request) -> web.Response:
        await motwi.delete_blocked(request.match_info["id"])
        return ok()

    # people

    @r.get("/api/people")
    async def people(_: web.Request) -> web.Response:
        return ok(await motwi.people())

    @r.post("/api/people")
    async def add_person(request: web.Request) -> web.Response:
        data = await _body(request)
        await motwi.add_person(_str(data, "kind"), _str(data, "login"))
        return ok(await motwi.people())

    @r.delete("/api/people/{kind}/{login}")
    async def remove_person(request: web.Request) -> web.Response:
        await motwi.remove_person(request.match_info["kind"], request.match_info["login"])
        return ok(await motwi.people())

    @r.get("/api/banned")
    async def banned(_: web.Request) -> web.Response:
        return ok(await motwi.banned())

    @r.post("/api/unban")
    async def unban(request: web.Request) -> web.Response:
        await motwi.unban(_str(await _body(request), "user_id"))
        return ok()

    # actions

    @r.get("/api/actions")
    async def actions(request: web.Request) -> web.Response:
        f, before, limit = _action_query(request.query)
        return ok([action_json(a) for a in await motwi.actions(f, before=before, limit=limit)])

    @r.post("/api/actions/{id}/{verb}")
    async def act(request: web.Request) -> web.Response:
        action_id = action_id_of(request)
        as_warn = (await _body(request)).get("as") == "warn"
        return ok(
            action_json(await motwi.act(action_id, request.match_info["verb"], as_warn=as_warn))
        )

    @r.get("/api/streams")
    async def streams(_: web.Request) -> web.Response:
        return ok(await motwi.store.list_streams(limit=50))

    @r.get("/api/chat")
    async def chat(_: web.Request) -> web.Response:
        data = await motwi.chat_view()
        data["recent"] = [action_json(a) for a in data["recent"]]
        data["open_requests"] = [action_json(a) for a in data["open_requests"]]
        data["present"] = mods.present(viewer=None)
        return ok(data)

    # stats

    @r.get("/api/overview")
    async def overview(_: web.Request) -> web.Response:
        data = await motwi.overview()
        data["open_requests"] = [action_json(a) for a in data["open_requests"]]
        data["recent"] = [action_json(a) for a in data["recent"]]
        data["warnings"] = [*data["warnings"], *await mods.warnings()]
        return ok(data)

    @r.get("/api/stats")
    async def stats(request: web.Request) -> web.Response:
        return ok(await motwi.stats(request.query.get("range", "7d")))

    @r.post("/api/try")
    async def try_message(request: web.Request) -> web.Response:
        return ok(await motwi.try_message(_str(await _body(request), "text")))

    # the browser extension

    @r.post("/api/pair")
    async def pair(request: web.Request) -> web.Response:
        if not request.headers.get("Origin", "").startswith(EXTENSION_ORIGIN):
            return _error(403, "forbidden origin")
        data = await _body(request)
        try:
            client, token = await pairing.redeem(_str(data, "code"), _str(data, "label", "Browser"))
        except PairingError as e:
            return _error(410, str(e))
        return ok({"token": token, "client_id": client.id, "channel": motwi.streamer_login})

    @r.post("/api/extension/leave")
    async def leave(request: web.Request) -> web.Response:
        # the extension disconnecting itself; the window has no such client and gets a 404
        client_id = pairing.check(request.headers.get("Authorization", "")[7:])
        if client_id is None:
            raise KeyError("client")
        await pairing.revoke(client_id)
        return ok()

    @r.get("/api/extension")
    async def extension(_: web.Request) -> web.Response:
        built = (extension_dir / "manifest.json").is_file()
        return ok(
            {"clients": await pairing.clients(), "folder": str(extension_dir) if built else None}
        )

    @r.post("/api/extension/pair")
    async def start_pairing(_: web.Request) -> web.Response:
        url = f"http://127.0.0.1:{port}/pair.html#c={pairing.start()}"
        await asyncio.get_running_loop().run_in_executor(None, open_url, url)
        return ok({"url": url, "expires_in": CODE_TTL_S})

    @r.post("/api/extension/reveal")
    async def reveal_folder(_: web.Request) -> web.Response:
        if not (extension_dir / "manifest.json").is_file():
            raise KeyError("extension")
        await asyncio.get_running_loop().run_in_executor(None, reveal, extension_dir)
        return ok()

    @r.delete("/api/extension/clients/{id}")
    async def revoke_client(request: web.Request) -> web.Response:
        await pairing.revoke(request.match_info["id"])
        return ok()

    # mods

    @r.get("/api/mods")
    async def mods_view(_: web.Request) -> web.Response:
        return ok(await mods.view())

    @r.post("/api/mods/invites")
    async def invite(_: web.Request) -> web.Response:
        return ok(await mods.invite(), status=201)

    @r.delete("/api/mods/invites/{id}")
    async def cancel_invite(request: web.Request) -> web.Response:
        await mods.cancel_invite(request.match_info["id"])
        return ok()

    @r.post("/api/mods/link/shared")
    async def link_shared(_: web.Request) -> web.Response:
        mods.mark_shared()
        return ok()

    @r.delete("/api/mods/clients/{id}")
    async def remove_mod(request: web.Request) -> web.Response:
        await mods.remove(request.match_info["id"])
        return ok()

    # Twitch accounts

    @r.post("/api/twitch/login")
    async def login(request: web.Request) -> web.Response:
        return ok(await motwi.begin_login(_role((await _body(request)).get("role"))))  # type: ignore[arg-type]

    @r.post("/api/twitch/cancel")
    async def cancel_login(request: web.Request) -> web.Response:
        motwi.cancel_login(_role((await _body(request)).get("role")))  # type: ignore[arg-type]
        return ok()

    @r.post("/api/twitch/logout")
    async def logout(request: web.Request) -> web.Response:
        await motwi.logout(_role((await _body(request)).get("role")))  # type: ignore[arg-type]
        return ok(await motwi.status())

    @r.post("/api/twitch/reconnect")
    async def reconnect(_: web.Request) -> web.Response:
        await motwi.reconnect()
        return ok(await motwi.status())

    # AI providers

    @r.get("/api/ai")
    async def ai(_: web.Request) -> web.Response:
        return ok(motwi.ai_config())

    @r.put("/api/ai/{slot}")
    async def set_ai(request: web.Request) -> web.Response:
        slot = _slot(request.match_info["slot"])
        data = await _body(request)
        key = data.get("key")
        return ok(
            await motwi.set_ai(
                slot,  # type: ignore[arg-type]
                _str(data, "provider"),
                data.get("model") if isinstance(data.get("model"), str) else None,
                data.get("base_url") if isinstance(data.get("base_url"), str) else None,
                key if isinstance(key, str) else None,
            )
        )

    @r.delete("/api/ai/backup")
    async def remove_backup(_: web.Request) -> web.Response:
        return ok(await motwi.remove_backup())

    @r.post("/api/ai/test")
    async def test_ai(request: web.Request) -> web.Response:
        data = await _body(request)

        def opt(name: str) -> str | None:
            value = data.get(name)
            return value if isinstance(value, str) else None

        return ok(
            await motwi.test_ai(
                _slot(data.get("slot", "primary")),  # type: ignore[arg-type]
                opt("provider"),
                opt("model"),
                opt("base_url"),
                opt("key"),
            )
        )

    return r


def _slot(value: Any) -> str:
    if value not in ("primary", "backup"):
        raise KeyError(value)
    return value


def _action_query(q) -> tuple[ActionFilter, int | None, int]:
    def enum_list(name: str, cls):
        raw = q.get(name)
        if not raw:
            return None
        try:
            return tuple(cls(v) for v in raw.split(","))
        except ValueError as e:
            raise FieldError(name, f"Unknown {name}.") from e

    def number(name: str) -> int | None:
        raw = q.get(name)
        if raw in (None, ""):
            return None
        try:
            return int(raw)
        except ValueError as e:
            raise FieldError(name, f"{name} must be a number.") from e

    source = q.get("source")
    try:
        source_value = Source(source) if source else None
    except ValueError as e:
        raise FieldError("source", "Unknown source.") from e
    f = ActionFilter(
        kinds=enum_list("kind", Kind),
        statuses=enum_list("status", Status),
        rule_id=q.get("rule") or None,
        source=source_value,
        user_login=q.get("user") or None,
        stream_id=q.get("stream") or None,
        actor=q.get("actor") or None,
        query=(q.get("q") or "").strip() or None,
    )
    limit = min(max(number("limit") or 50, 1), 200)
    return f, number("before"), limit


# events


def _allowed_origins(port: int) -> set[str]:
    return {f"http://{h}" for h in _allowed_hosts(port)}


async def _events(request: web.Request) -> web.StreamResponse:
    motwi: Motwi = request.app[MOTWI]
    pairing = request.app[PAIRING]
    origin = request.headers.get("Origin")
    from_extension = origin is not None and origin.startswith(EXTENSION_ORIGIN)
    if origin is not None and not from_extension and origin not in request.app[ORIGINS]:
        return _error(403, "forbidden origin")
    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=64 * 1024)
    await ws.prepare(request)
    try:
        hello = await ws.receive(timeout=HELLO_TIMEOUT_S)
    except TimeoutError:
        await ws.close(code=4401, message=b"no hello")
        return ws
    token = None
    if hello.type == WSMsgType.TEXT:
        try:
            frame = json.loads(hello.data)
            token = frame.get("token") if isinstance(frame, dict) else None
            if not isinstance(token, str):
                token = None
        except ValueError:
            token = None
    client_id: str | None = None
    if from_extension or request.app[AUTH].check(token) is None:  # never the window's token
        client_id = pairing.check(token)
        if client_id is None:
            await ws.close(code=4401, message=b"not signed in to Motwi")
            return ws
        await pairing.attach(client_id, ws)
    log.info("event socket opened (%s)", "window" if client_id is None else "extension")

    sockets = request.app[SOCKETS]
    sockets.add(ws)
    try:
        await pump_events(ws, motwi, motwi.status, extension_events=client_id is None)
    finally:
        sockets.discard(ws)
        if client_id is not None:
            pairing.detach(client_id, ws)
        await ws.close()
    return ws


# app


def create_app(
    motwi: Motwi,
    *,
    auth: ApiAuth,
    port: int = DEFAULT_PORT,
    ui_dir: Path | None = None,
    on_show: Callable[[], None] | None = None,
    on_quit: Callable[[], None] | None = None,
    autostart: AutoStart | None = None,
    open_url: Callable[[str], Any] = webbrowser.open,
    reveal: Callable[[Path], Any] = _reveal_folder,
    extension_dir: Path | None = None,
    tunnel: TunnelLike | None = None,
    identify: Identify | None = None,
) -> web.Application:
    app = web.Application(middlewares=_middlewares(port), client_max_size=256 * 1024)
    pairing = Pairing(motwi.store, motwi.bus)
    folder = extension_dir or default_extension_dir()
    mods = ModAccess(
        motwi,
        pairing,
        tunnel=tunnel or Tunnel(cloudflared_exe(), data_dir() / "cloudflared.yml"),
        make_gateway=lambda m: create_gateway(
            m, motwi, pairing, ui_dir=ui_dir, extension_dir=folder
        ),
        identify=identify,
    )
    app[MODS] = mods
    app[MOTWI] = motwi
    app[AUTH] = auth
    app[PAIRING] = pairing
    app[ORIGINS] = _allowed_origins(port)
    app[SOCKETS] = set()

    async def load_pairing(_: web.Application) -> None:
        await pairing.load()
        await mods.start()

    async def stop_mods(_: web.Application) -> None:
        await mods.close()

    async def close_sockets(a: web.Application) -> None:
        # otherwise shutdown waits on them, and quitting looks like a hang
        for ws in list(a[SOCKETS]):
            await ws.close(code=1001, message=b"Motwi is shutting down")

    app.on_startup.append(load_pairing)
    app.on_shutdown.append(close_sockets)
    app.on_cleanup.append(stop_mods)
    app.add_routes(
        _routes(
            motwi,
            pairing,
            mods,
            port=port,
            on_show=on_show,
            on_quit=on_quit,
            autostart=autostart or AutoStart(),
            open_url=open_url,
            reveal=reveal,
            extension_dir=folder,
        )
    )
    app.router.add_get("/api/events", _events)
    if ui_dir is not None and (ui_dir / "index.html").is_file():
        index = ui_dir / "index.html"

        async def root(_: web.Request) -> web.FileResponse:
            return web.FileResponse(index)

        app.router.add_get("/", root)
        if (ui_dir / "assets").is_dir():
            app.router.add_static("/assets", ui_dir / "assets")
        for f in ui_dir.iterdir():
            if f.is_file() and f.name != "index.html":
                app.router.add_get(f"/{f.name}", lambda _, f=f: web.FileResponse(f))
    else:

        async def missing(_: web.Request) -> web.Response:
            return web.Response(
                text="Motwi's window files are missing. Build them with: cd ui && npm run build",
                content_type="text/plain",
                status=503,
            )

        app.router.add_get("/", missing)
    return app


class PortInUse(OSError):
    pass


async def serve(app: web.Application, port: int = DEFAULT_PORT) -> web.AppRunner:
    runner = web.AppRunner(app, access_log=None, handle_signals=False)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port, reuse_address=False)
    try:
        await site.start()
    except OSError as e:
        await runner.cleanup()
        raise PortInUse(f"port {port} is already in use") from e
    return runner
