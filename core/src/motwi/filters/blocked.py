"""Blocked-word filter types."""

from dataclasses import dataclass

from motwi.models import Ceiling


@dataclass(frozen=True)
class BlockedWord:
    id: str
    phrase: str
    base_index: int = -1
    ceiling: Ceiling = Ceiling.TIMEOUT
