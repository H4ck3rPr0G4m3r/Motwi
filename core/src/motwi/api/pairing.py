"""Pairing a browser with this Motwi: a one-time code, redeemed for a revocable token.

Tokens are random 256-bit values, so storing a plain SHA-256 of each is enough: a copy of
the database can't be turned back into a working token.
"""

import hashlib
import hmac
import secrets
import time
from collections.abc import Callable

from aiohttp import web

from motwi.events import EventBus, ExtensionChanged
from motwi.store import ClientRecord, Store

CODE_TTL_S = 600
LABEL_MAX = 60


class PairingError(Exception):
    """The pairing code is wrong, already used, or too old."""


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Pairing:
    def __init__(self, store: Store, bus: EventBus, clock: Callable[[], float] = time.time) -> None:
        self._store = store
        self._bus = bus
        self._clock = clock
        self._code: tuple[str, float] | None = None
        self._ids_by_hash: dict[str, str] = {}
        self._sockets: dict[str, set[web.WebSocketResponse]] = {}

    async def load(self) -> None:
        self._ids_by_hash = {c.token_hash: c.id for c in await self._store.list_clients()}

    def start(self) -> str:
        """A fresh one-time code. It replaces any earlier one."""
        code = secrets.token_urlsafe(32)
        self._code = (code, self._clock() + CODE_TTL_S)
        return code

    async def redeem(self, code: str, label: str) -> tuple[ClientRecord, str]:
        current = self._code
        if current is None or not hmac.compare_digest(current[0].encode(), code.encode()):
            # a wrong guess doesn't burn the real code
            raise PairingError("This link has already been used. Connect again from Motwi.")
        self._code = None
        if self._clock() > current[1]:
            raise PairingError("This link has expired. Connect again from Motwi.")
        token = secrets.token_urlsafe(32)
        client = await self._store.add_client(
            "extension", " ".join(label.split())[:LABEL_MAX] or "Browser", _hash(token)
        )
        self._ids_by_hash[client.token_hash] = client.id
        self._bus.publish(ExtensionChanged())
        return client, token

    def check(self, token: str | None) -> str | None:
        """The client id for a valid token, else None."""
        return self._ids_by_hash.get(_hash(token)) if token else None

    async def revoke(self, client_id: str) -> None:
        if not await self._store.delete_client(client_id):
            raise KeyError(client_id)
        self._ids_by_hash = {h: i for h, i in self._ids_by_hash.items() if i != client_id}
        for ws in list(self._sockets.pop(client_id, ())):
            await ws.close(code=4401, message=b"disconnected from Motwi")
        self._bus.publish(ExtensionChanged())

    async def clients(self) -> list[dict]:
        return [
            {
                "id": c.id,
                "label": c.label,
                "created_at": c.created_at,
                "last_seen": c.last_seen,
                "connected": bool(self._sockets.get(c.id)),
            }
            for c in await self._store.list_clients()
        ]

    async def attach(self, client_id: str, ws: web.WebSocketResponse) -> None:
        self._sockets.setdefault(client_id, set()).add(ws)
        await self._store.touch_client(client_id, self._clock())
        self._bus.publish(ExtensionChanged())

    def detach(self, client_id: str, ws: web.WebSocketResponse) -> None:
        sockets = self._sockets.get(client_id)
        if sockets is not None:
            sockets.discard(ws)
            if not sockets:
                del self._sockets[client_id]
        self._bus.publish(ExtensionChanged())
