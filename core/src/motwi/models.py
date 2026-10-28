"""Shared value types for the Motwi core engine.

Everything here is free of I/O so that every other module can depend on it.
The dataclasses are frozen; module-level tables such as PRESETS must not be mutated.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


class Kind(StrEnum):
    NONE = "none"
    DELETE = "delete"
    WARN = "warn"
    TIMEOUT = "timeout"
    BAN_REQUEST = "ban_request"


class Ceiling(StrEnum):
    DELETE = "delete"
    WARN = "warn"
    TIMEOUT = "timeout"
    BAN = "ban"


class Source(StrEnum):
    BLOCKED_WORD = "blocked_word"
    SPAM = "spam"
    AI = "ai"


class Status(StrEnum):
    SUGGESTED = "suggested"
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"
    UNDONE = "undone"
    DENIED = "denied"
    IGNORED = "ignored"
    EXPIRED = "expired"


@dataclass(frozen=True)
class ChatMessage:
    id: str
    user_id: str
    user_login: str
    display_name: str
    text: str
    emote_free_text: str
    badges: frozenset[str]
    ts: float

    @property
    def is_broadcaster(self) -> bool:
        return "broadcaster" in self.badges

    @property
    def is_mod(self) -> bool:
        return "moderator" in self.badges

    @property
    def is_vip(self) -> bool:
        return "vip" in self.badges


@dataclass(frozen=True)
class Rule:
    id: str
    text: str
    ceiling: Ceiling
    scope: Literal["community", "stream"]


@dataclass(frozen=True)
class Thresholds:
    warn: float
    timeout: float
    ban: float

    def __post_init__(self) -> None:
        if not (0 <= self.warn < self.timeout < self.ban <= 10):
            raise ValueError("thresholds must satisfy 0 <= warn < timeout < ban <= 10")


PRESETS: dict[str, Thresholds] = {
    "relaxed": Thresholds(7.0, 8.5, 9.5),
    "standard": Thresholds(6.0, 7.5, 9.0),
    "strict": Thresholds(5.0, 6.5, 8.0),
}


@dataclass(frozen=True)
class LadderStep:
    kind: Kind
    duration_s: int | None = None


DEFAULT_LADDER: tuple[LadderStep, ...] = (
    LadderStep(Kind.WARN),
    LadderStep(Kind.TIMEOUT, 60),
    LadderStep(Kind.TIMEOUT, 600),
    LadderStep(Kind.BAN_REQUEST),
)


@dataclass(frozen=True)
class Verdict:
    message_id: str
    score: float
    rule_id: str | None
    reason: str


@dataclass(frozen=True)
class Unchecked:
    message_id: str
    reason: str


@dataclass(frozen=True)
class StreamContext:
    title: str | None = None
    game: str | None = None
    stream_id: str | None = None
