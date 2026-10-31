"""What the local API and the mods' gateway share: responses, error mapping, event streams."""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import fields
from enum import Enum
from typing import Any

from aiohttp import web

from motwi.events import (
    ActionChanged,
    AIStateChanged,
    BanRequestReminder,
    ExtensionChanged,
    LoginFinished,
    RecapReady,
    StatusChanged,
    StreamChanged,
)
from motwi.moderation import AlreadyResolved, ExecutorError
from motwi.service import FieldError, Motwi, NotConnected
from motwi.store import ActionRecord
from motwi.twitch.auth import AuthError, AuthExpired
from motwi.twitch.helix import HelixError

log = logging.getLogger(__name__)

STATUS_DEBOUNCE_S = 0.2
EXTENSION_ORIGIN = "chrome-extension://"
VERBS = ("approve", "deny", "undo", "retry")

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


def _plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (list, tuple, frozenset, set)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if hasattr(value, "__dataclass_fields__"):
        return {f.name: _plain(getattr(value, f.name)) for f in fields(value)}
    return value


def action_json(a: ActionRecord) -> dict:
    return _plain(a)


def dumps(data: Any) -> str:
    return json.dumps(_plain(data), ensure_ascii=False)


def ok(data: Any = None, status: int = 200) -> web.Response:
    return web.json_response({"ok": True} if data is None else data, status=status, dumps=dumps)


def error(status: int, message: str, **extra: Any) -> web.Response:
    return web.json_response({"error": message, **extra}, status=status)


async def body(request: web.Request) -> dict:
    if not request.can_read_body:
        return {}
    data = await request.json()
    if not isinstance(data, dict):
        raise FieldError("", "Expected a JSON object.")
    return data


def text_field(data: dict, name: str, default: str | None = None) -> str:
    value = data.get(name, default)
    if not isinstance(value, str):
        raise FieldError(name, f"{name} is required.")
    return value


def action_id_of(request: web.Request) -> int:
    if request.match_info["verb"] not in VERBS:
        raise KeyError(request.match_info["verb"])
    try:
        return int(request.match_info["id"])
    except ValueError as e:
        raise KeyError(request.match_info["id"]) from e


async def guarded(request: web.Request, handler: Handler) -> web.StreamResponse:
    """Run a handler, turning what it can raise into a clean JSON error."""
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except FieldError as e:
        return error(422, str(e), field=e.field)
    except (NotConnected, AlreadyResolved) as e:
        return error(409, str(e))
    except KeyError:
        return error(404, "not found")
    except AuthExpired as e:
        return error(409, f"{e}. Reconnect Twitch.")
    except (ExecutorError, AuthError, HelixError) as e:
        return error(502, str(e))
    except json.JSONDecodeError:
        return error(400, "the request body isn't valid JSON")
    except Exception:
        log.exception("%s %s failed", request.method, request.path)
        return error(500, "Motwi hit an unexpected error. See the log.")


async def pump_events(
    ws: web.WebSocketResponse,
    motwi: Motwi,
    status: Callable[[], Awaitable[dict]],
    *,
    logins: bool = True,
    extension_events: bool = False,
) -> None:
    """Send hello, then forward events until the client leaves."""
    q = motwi.bus.subscribe()
    try:
        await ws.send_str(dumps({"type": "hello", "status": await status()}))
    except ConnectionResetError:  # gone already, or just removed
        motwi.bus.unsubscribe(q)
        return
    dirty = asyncio.Event()

    async def send_status() -> None:
        while True:
            await dirty.wait()
            await asyncio.sleep(STATUS_DEBOUNCE_S)
            dirty.clear()
            await ws.send_str(dumps({"type": "status", "status": await status()}))

    async def forward() -> None:
        while True:
            e = await q.get()
            if isinstance(e, ActionChanged):
                await ws.send_str(dumps({"type": "action", "action": action_json(e.action)}))
                dirty.set()  # open request counts change with actions
            elif isinstance(e, BanRequestReminder):
                await ws.send_str(dumps({"type": "reminder", "action": action_json(e.action)}))
            elif isinstance(e, LoginFinished):
                if logins:
                    await ws.send_str(
                        dumps(
                            {
                                "type": "login",
                                "role": e.role,
                                "ok": e.ok,
                                "login": e.login,
                                "error": e.error,
                            }
                        )
                    )
            elif isinstance(e, (StatusChanged, AIStateChanged, StreamChanged, RecapReady)):
                dirty.set()
            elif isinstance(e, ExtensionChanged):
                dirty.set()  # who's connected is part of who's looking at a request
                if extension_events:
                    await ws.send_str(dumps({"type": "extension"}))

    async def drain() -> None:
        async for _ in ws:  # only keepalives after hello; ends when the client leaves
            pass

    tasks = [asyncio.create_task(c) for c in (send_status(), forward(), drain())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        motwi.bus.unsubscribe(q)
