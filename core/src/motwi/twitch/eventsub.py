"""EventSub WebSocket session: welcome, subscribe, keepalive, reconnect, dedup."""

import asyncio
import json
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from websockets.exceptions import WebSocketException

from motwi.twitch.helix import HelixError

log = logging.getLogger(__name__)

WS_URL = "wss://eventsub.wss.twitch.tv/ws?keepalive_timeout_seconds=30"
WELCOME_TIMEOUT_S = 10.0
KEEPALIVE_GRACE_S = 5.0
MAX_BACKOFF_S = 60.0
SEEN_IDS = 1000


class Socket(Protocol):
    async def recv(self) -> str | bytes: ...
    async def close(self) -> None: ...


class _Dropped(Exception):
    """The connection died or went silent; start over with a fresh session."""


def _parse(raw: str | bytes) -> dict[str, Any]:
    try:
        msg = json.loads(raw)
    except ValueError as e:
        raise _Dropped("unreadable frame") from e
    if not isinstance(msg, dict):
        raise _Dropped("unexpected frame")
    return msg


class EventSubClient:
    """Keeps one EventSub session alive and hands each notification to `on_event`.

    `subscribe(session_id)` runs after every fresh welcome (not after a Twitch-initiated
    reconnect, which carries subscriptions over). `on_event(type, event, timestamp)` is
    called synchronously, in order; it must not block.
    """

    def __init__(
        self,
        *,
        connect: Callable[[str], Awaitable[Socket]],
        subscribe: Callable[[str], Awaitable[None]],
        on_event: Callable[[str, dict[str, Any], str], None],
        on_state: Callable[[str, str], None] = lambda state, detail: None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        url: str = WS_URL,
    ) -> None:
        self._connect = connect
        self._subscribe = subscribe
        self._on_event = on_event
        self._on_state = on_state
        self._sleep = sleep
        self._url = url
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._keepalive_s = 30.0
        self._ws: Socket | None = None

    async def run(self) -> None:
        """Run until cancelled. Auth and configuration errors from `subscribe` propagate."""
        backoff = 1.0
        while True:
            try:
                ws = await self._connect(self._url)
            except (OSError, TimeoutError, WebSocketException) as e:  # network, DNS, handshake
                log.warning("EventSub connect failed: %s", e)
                self._on_state("disconnected", f"can't reach Twitch, retrying in {backoff:g}s")
                await self._sleep(backoff)
                backoff = min(MAX_BACKOFF_S, backoff * 2)
                continue
            self._ws = ws
            try:
                session_id = await self._welcome(ws)
                await self._subscribe(session_id)
                self._on_state("connected", "")
                backoff = 1.0
                await self._serve(ws)
            except _Dropped as e:
                log.warning("EventSub connection dropped: %s", e)
            except HelixError as e:
                if e.status not in (0, 429) and e.status < 500:
                    raise
                log.warning("EventSub subscribe failed: %s", e)
            finally:
                await _close(self._ws or ws)
                self._ws = None
            self._on_state("disconnected", f"reconnecting in {backoff:g}s")
            await self._sleep(backoff)
            backoff = min(MAX_BACKOFF_S, backoff * 2)

    async def _recv(self, ws: Socket, timeout: float) -> dict[str, Any]:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout)
        except TimeoutError as e:
            raise _Dropped("no message within keepalive window") from e
        except asyncio.CancelledError:
            raise
        except Exception as e:  # connection closed
            raise _Dropped(str(e) or type(e).__name__) from e
        return _parse(raw)

    async def _welcome(self, ws: Socket) -> str:
        msg = await self._recv(ws, WELCOME_TIMEOUT_S)
        if msg.get("metadata", {}).get("message_type") != "session_welcome":
            raise _Dropped("expected session_welcome")
        session = msg["payload"]["session"]
        self._keepalive_s = float(session.get("keepalive_timeout_seconds") or 30)
        return str(session["id"])

    async def _serve(self, ws: Socket) -> None:
        while True:
            msg = await self._recv(ws, self._keepalive_s + KEEPALIVE_GRACE_S)
            kind = msg.get("metadata", {}).get("message_type")
            if kind == "session_reconnect":
                url = msg["payload"]["session"]["reconnect_url"]
                ws = self._ws = await self._switch(ws, url)
            else:
                self._dispatch(msg)

    async def _switch(self, old: Socket, url: str) -> Socket:
        """Twitch asked us to move: keep reading the old socket until the new one is welcomed."""
        log.info("EventSub reconnect requested")

        async def open_new() -> Socket:
            new = await self._connect(url)
            try:
                await self._welcome(new)
            except BaseException:
                await _close(new)
                raise
            return new

        new_task = asyncio.ensure_future(open_new())
        try:
            while not new_task.done():
                recv = asyncio.ensure_future(old.recv())
                done, _ = await asyncio.wait({recv, new_task}, return_when=asyncio.FIRST_COMPLETED)
                if recv not in done:
                    recv.cancel()
                    break
                try:
                    self._dispatch(_parse(recv.result()))
                except (_Dropped, OSError, WebSocketException):
                    await asyncio.wait({new_task})  # old socket closed; just wait for the new one
            try:
                return new_task.result()
            except Exception as e:
                raise _Dropped(f"reconnect failed: {e}") from e
        finally:
            if not new_task.done():
                new_task.cancel()
            await _close(old)

    def _dispatch(self, msg: dict[str, Any]) -> None:
        meta = msg.get("metadata", {})
        kind = meta.get("message_type")
        if kind not in ("notification", "revocation"):
            return  # keepalives only reset the timer
        message_id = meta.get("message_id")
        if message_id:
            if message_id in self._seen:
                return
            self._seen[message_id] = None
            if len(self._seen) > SEEN_IDS:
                self._seen.popitem(last=False)
        payload = msg.get("payload", {})
        sub = payload.get("subscription", {})
        if kind == "revocation":
            log.warning("EventSub subscription %s revoked: %s", sub.get("type"), sub.get("status"))
            self._on_state("revoked", f"{sub.get('type')}: {sub.get('status')}")
            return
        try:
            self._on_event(
                str(sub.get("type")), payload.get("event", {}), meta.get("message_timestamp", "")
            )
        except Exception:
            log.exception("EventSub event handler failed for %s", sub.get("type"))


async def _close(ws: Socket) -> None:
    try:
        await ws.close()
    except Exception:
        log.debug("closing EventSub socket failed", exc_info=True)
