"""In-memory per-user offense history used for ladder escalation."""


class OffenseTracker:
    def __init__(self, inactivity_reset_s: float = 43_200) -> None:
        self._reset_after = inactivity_reset_s
        # user_id -> [(action_id, ladder_index, ts)], oldest first
        self._offenses: dict[str, list[tuple[int, int, float]]] = {}

    def _live(self, user_id: str, now: float) -> list[tuple[int, int, float]]:
        entries = self._offenses.get(user_id)
        if not entries:
            return []
        if now - entries[-1][2] > self._reset_after:
            del self._offenses[user_id]
            return []
        return entries

    def last_index(self, user_id: str, now: float) -> int | None:
        entries = self._live(user_id, now)
        return entries[-1][1] if entries else None

    def count(self, user_id: str, now: float) -> int:
        return len(self._live(user_id, now))

    def record(self, user_id: str, ladder_index: int, action_id: int, now: float) -> None:
        self._offenses.setdefault(user_id, []).append((action_id, ladder_index, now))

    def undo(self, action_id: int) -> None:
        for user_id, entries in self._offenses.items():
            kept = [e for e in entries if e[0] != action_id]
            if len(kept) != len(entries):
                if kept:
                    self._offenses[user_id] = kept
                else:
                    del self._offenses[user_id]
                return

    def reset(self) -> None:
        self._offenses.clear()
