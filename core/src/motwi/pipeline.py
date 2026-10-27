"""Message pipeline: exemptions, blocked words, spam filter, AI scoring, then moderation."""

import asyncio
import time
from collections import deque
from collections.abc import Callable

from motwi.ai.prompt import BatchItem
from motwi.ai.scorer import Scorer
from motwi.engine.decide import NO_ACTION, Plan, decide_ai, decide_filter
from motwi.engine.offenses import OffenseTracker
from motwi.filters.spam import SpamFilter, SpamVerdict
from motwi.live import LiveConfig
from motwi.models import Ceiling, ChatMessage, Source, Unchecked
from motwi.moderation import ModerationService, Outcome
from motwi.store import ActionRecord, Store

CONTEXT_BUFFER = 50
RECORDED_CONTEXT_LINES = 5


class Pipeline:
    def __init__(
        self,
        *,
        live: LiveConfig,
        spam: SpamFilter,
        scorer: Scorer,
        moderation: ModerationService,
        offenses: OffenseTracker,
        store: Store,
        self_user_ids: set[str],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._live = live
        self._spam = spam
        self._scorer = scorer
        self._moderation = moderation
        self._offenses = offenses
        self._store = store
        self._self_user_ids = self_user_ids
        self._clock = clock
        self._context: deque[ChatMessage] = deque(maxlen=CONTEXT_BUFFER)
        self._known: set[str] = set()
        self._user_locks: dict[str, asyncio.Lock] = {}

    def reset_stream(self) -> None:
        self._offenses.reset()
        self._spam.reset()
        self._known.clear()
        self._context.clear()
        self._user_locks.clear()

    async def handle(self, msg: ChatMessage) -> ActionRecord | None:
        # Everything up to the first await is synchronous, so concurrent handle() calls
        # each see a consistent snapshot of the buffer as it was just before their message.
        known_chatter = msg.user_id in self._known
        before = list(self._context)
        self._context.append(msg)
        self._known.add(msg.user_id)

        if self._exempt(msg):
            return None

        live = self._live
        ladder = live.settings.ladder
        recorded = [f"{m.user_login}: {m.text}" for m in before[-RECORDED_CONTEXT_LINES:]]

        word = live.blocked.match(msg.text)
        if word is not None:
            return await self._act(
                msg,
                recorded,
                lambda last: Outcome(
                    Source.BLOCKED_WORD,
                    decide_filter(
                        base_index=word.base_index,
                        ceiling=word.ceiling,
                        ladder=ladder,
                        last_index=last,
                    ),
                    None,
                    None,
                    f"Blocked word: {word.phrase}",
                ),
            )

        spam = self._spam.check(msg, known_chatter=known_chatter)
        if spam.verdict is SpamVerdict.SPAM:
            return await self._act(
                msg,
                recorded,
                lambda last: Outcome(
                    Source.SPAM,
                    decide_filter(
                        base_index=-1, ceiling=Ceiling.TIMEOUT, ladder=ladder, last_index=last
                    ),
                    None,
                    None,
                    f"Spam filter: {spam.reason}",
                ),
            )
        recent = spam.recent if spam.verdict is SpamVerdict.BORDERLINE else ()

        n = live.settings.context_lines
        window = before[-n:] if n > 0 else []
        result = await self._scorer.score(BatchItem(msg, recent), window)
        if isinstance(result, Unchecked):
            await self._store.record_unchecked(msg.id, msg.user_login, result.reason)
            return None

        verdict = result
        leeway = (
            live.settings.vip_leeway if msg.is_vip or msg.user_login.lower() in live.vips else 0.0
        )

        def ai_outcome(last: int | None) -> Outcome:
            # Read config here, after any wait for the user's lock, so reloads are honoured.
            plan: Plan = decide_ai(
                verdict,
                rules=self._live.rules_by_id(),
                thresholds=self._live.settings.thresholds(),
                ladder=self._live.settings.ladder,
                last_index=last,
                vip_leeway=leeway,
            )
            return Outcome(Source.AI, plan, verdict.score, verdict.rule_id, verdict.reason)

        return await self._act(msg, recorded, ai_outcome)

    def _exempt(self, msg: ChatMessage) -> bool:
        return (
            msg.is_broadcaster
            or msg.is_mod
            or msg.user_id in self._self_user_ids
            or msg.user_login.lower() in self._live.trusted
        )

    async def _act(
        self,
        msg: ChatMessage,
        recorded_context: list[str],
        make_outcome: Callable[[int | None], Outcome],
    ) -> ActionRecord | None:
        """Decide and apply under a per-user lock so concurrent messages escalate in order."""
        lock = self._user_locks.setdefault(msg.user_id, asyncio.Lock())
        async with lock:
            outcome = make_outcome(self._offenses.last_index(msg.user_id, self._clock()))
            if outcome.plan == NO_ACTION:
                return None
            return await self._moderation.apply(msg, outcome, recorded_context)
