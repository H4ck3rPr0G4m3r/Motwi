"""In-process event bus that fans engine events out to subscribers."""

import asyncio
from dataclasses import dataclass

from motwi.models import StreamContext
from motwi.store import ActionRecord


@dataclass(frozen=True)
class ActionChanged:
    action: ActionRecord


@dataclass(frozen=True)
class AIStateChanged:
    state: str
    detail: str


@dataclass(frozen=True)
class BanRequestReminder:
    action: ActionRecord


@dataclass(frozen=True)
class StreamChanged:
    stream: StreamContext


@dataclass(frozen=True)
class StatusChanged:
    """Something in the app's status changed (Twitch, engine, AI setup); re-read it."""


@dataclass(frozen=True)
class LoginFinished:
    role: str
    ok: bool
    login: str | None
    error: str | None


Event = (
    ActionChanged
    | AIStateChanged
    | BanRequestReminder
    | StreamChanged
    | StatusChanged
    | LoginFinished
)


class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[Event]] = []

    def subscribe(self, maxsize: int = 500) -> asyncio.Queue[Event]:
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=maxsize)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[Event]) -> None:
        if q in self._subscribers:
            self._subscribers.remove(q)

    def publish(self, e: Event) -> None:
        """Deliver to every subscriber without blocking; a full queue loses its oldest event."""
        for q in self._subscribers:
            if q.full():
                q.get_nowait()
            q.put_nowait(e)
