"""Conservative spam filter.

Only objective, per-user signals count: message flooding, near-identical
repeats, promo links/phrases, character floods and mass mentions. Caps,
emotes and the same text from many different users (copypasta) never do.
"""

import re
from collections import OrderedDict, deque
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum

from motwi.models import ChatMessage

_TLDS = "com|net|org|shop|xyz|io|gg|tv|me|ru|store|site|online|live|top|click|link"
_LINK = re.compile(
    r"https?://|\bwww\."
    # name, optional spaces, then a literal dot ("strmboost .shop") ...
    rf"|[a-z0-9-]+\s*\.(?:{_TLDS})\b"
    # ... or a spelled-out dot. Spaces after a literal dot are not accepted:
    # "I died. me too" must not look like a link.
    rf"|[a-z0-9-]+(?:\s+dot\s+|\s*\(dot\)\s*|\s*\[dot\]\s*)(?:{_TLDS})\b"
)
_SPAM_PHRASES = (
    "cheap viewers",
    "buy followers",
    "best viewers",
    "promote your stream",
    "boost your stream",
    "become famous",
    "viewers and followers",
)
_MENTION = re.compile(r"(?<!\w)@(\w{2,})")


class SpamVerdict(StrEnum):
    OK = "ok"
    BORDERLINE = "borderline"
    SPAM = "spam"


@dataclass(frozen=True)
class SpamConfig:
    flood_count: int = 6
    flood_window_s: float = 5.0
    repeat_count: int = 3
    repeat_window_s: float = 30.0
    repeat_min_len: int = 10
    repeat_similarity: float = 0.9
    char_run: int = 60
    mass_mentions: int = 5
    history_size: int = 6


@dataclass(frozen=True)
class SpamResult:
    verdict: SpamVerdict
    reason: str
    recent: tuple[str, ...]


_OK = SpamResult(SpamVerdict.OK, "", ())


class SpamFilter:
    def __init__(self, config: SpamConfig, *, max_users: int = 5_000) -> None:
        self._cfg = config
        self._max_users = max_users
        self._horizon = max(config.flood_window_s, config.repeat_window_s)
        self._maxlen = max(config.history_size, config.flood_count, config.repeat_count)
        self._history: OrderedDict[str, deque[tuple[float, str, str]]] = OrderedDict()
        self._char_run = re.compile(rf"(\S)\1{{{max(config.char_run - 1, 1)},}}")

    def reset(self) -> None:
        self._history.clear()

    def recent_for(self, user_id: str) -> tuple[str, ...]:
        hist = self._history.get(user_id)
        if not hist:
            return ()
        return tuple(raw for _, _, raw in hist)[-self._cfg.history_size :]

    def check(self, msg: ChatMessage, *, known_chatter: bool) -> SpamResult:
        hist = self._touch(msg.user_id)
        while hist and hist[0][0] < msg.ts - self._horizon:
            hist.popleft()
        norm = " ".join(msg.emote_free_text.lower().split())
        hist.append((msg.ts, norm, msg.text))

        cfg = self._cfg
        lowered = " ".join(msg.text.lower().split())
        has_link = _LINK.search(lowered) is not None
        has_phrase = any(p in lowered for p in _SPAM_PHRASES)

        if self._char_run.search(msg.text):
            return self._spam("character flood")
        if len({m.lower() for m in _MENTION.findall(msg.text)}) >= cfg.mass_mentions:
            return self._spam("mass mentions")
        if has_link and has_phrase:
            return self._spam("link spam")

        in_flood = sum(1 for ts, _, _ in hist if ts >= msg.ts - cfg.flood_window_s)
        if in_flood >= cfg.flood_count:
            return self._spam("flood")
        if len(norm) >= cfg.repeat_min_len:
            similar = sum(
                1
                for ts, other, _ in hist
                if ts >= msg.ts - cfg.repeat_window_s
                and SequenceMatcher(None, other, norm).ratio() >= cfg.repeat_similarity
            )
            if similar >= cfg.repeat_count:
                return self._spam("repeat")

        if in_flood >= max(2, cfg.flood_count - 2):
            return self._borderline("flood", hist)
        if has_link and not known_chatter:
            return self._borderline("link from new chatter", hist)
        if has_phrase and not known_chatter:
            return self._borderline("spam phrase", hist)
        return _OK

    def _touch(self, user_id: str) -> deque[tuple[float, str, str]]:
        hist = self._history.get(user_id)
        if hist is None:
            hist = self._history[user_id] = deque(maxlen=self._maxlen)
            while len(self._history) > self._max_users:
                self._history.popitem(last=False)
        else:
            self._history.move_to_end(user_id)
        return hist

    @staticmethod
    def _spam(reason: str) -> SpamResult:
        return SpamResult(SpamVerdict.SPAM, reason, ())

    def _borderline(self, reason: str, hist: deque[tuple[float, str, str]]) -> SpamResult:
        recent = tuple(raw for _, _, raw in hist)[-self._cfg.history_size :]
        return SpamResult(SpamVerdict.BORDERLINE, reason, recent)
