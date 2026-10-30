"""Blocked-word filter types."""

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import groupby

from motwi.models import Ceiling

_ZERO_WIDTH = re.compile("[\u200b-\u200f\u2060\ufeff]")
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a",
                       "5": "s", "7": "t", "@": "a", "$": "s"})
_SPLIT = re.compile(r"[^a-z0-9]+")
_REPEAT = re.compile(r"(.)\1{2,}")
_SEVERITY = {Ceiling.DELETE: 0, Ceiling.WARN: 1, Ceiling.TIMEOUT: 2, Ceiling.BAN: 3}


@dataclass(frozen=True)
class BlockedWord:
    id: str
    phrase: str
    base_index: int = -1
    ceiling: Ceiling = Ceiling.TIMEOUT
    hidden: bool = False  # shown only as a count, for slurs


def normalize_tokens(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text).lower()
    text = _ZERO_WIDTH.sub("", text)
    text = "".join(c for c in text if not unicodedata.category(c).startswith("M"))
    tokens = [t for t in _SPLIT.split(text.translate(_LEET)) if t]

    merged: list[str] = []
    for is_single_char, group in groupby(tokens, key=lambda t: len(t) == 1):
        run = list(group)
        if is_single_char and len(run) >= 2:
            merged.append("".join(run))
        else:
            merged.extend(run)

    return [_REPEAT.sub(r"\1\1", t) for t in merged]


class BlockedWords:
    def __init__(self, words: Iterable[BlockedWord]):
        self._entries: list[tuple[BlockedWord, list[str]]] = []
        for word in words:
            phrase = normalize_tokens(word.phrase)
            if phrase:
                self._entries.append((word, phrase))

    def match(self, text: str) -> BlockedWord | None:
        tokens = normalize_tokens(text)
        hits = [word for word, phrase in self._entries if _contains(tokens, phrase)]
        return max(hits, key=_rank, default=None)


def _contains(tokens: list[str], phrase: list[str]) -> bool:
    n = len(phrase)
    return any(tokens[i:i + n] == phrase for i in range(len(tokens) - n + 1))


def _rank(word: BlockedWord) -> tuple[int, int]:
    return _SEVERITY[word.ceiling], word.base_index
