"""Live moderation: the M1 engine wired to a real Twitch channel."""

import asyncio
import logging
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from typing import Any, TextIO

import httpx

from motwi.ai.client import LLMClient
from motwi.ai.providers import PROVIDERS, client_for
from motwi.ai.scorer import AIState, Scorer
from motwi.engine.offenses import OffenseTracker
from motwi.events import (
    ActionChanged,
    AIStateChanged,
    BanRequestReminder,
    EventBus,
    RecapReady,
    StreamChanged,
)
from motwi.filters.spam import SpamFilter
from motwi.live import LiveConfig
from motwi.models import ChatMessage, StreamContext
from motwi.moderation import Executor, ModerationService
from motwi.pipeline import Pipeline
from motwi.recap import recap_summary
from motwi.settings import ProviderConfig, Settings
from motwi.sim.printer import format_action
from motwi.starter import STARTER_RULES
from motwi.store import Store
from motwi.twitch.convert import chat_message, parse_ts
from motwi.twitch.tokens import SecretStore, ai_key_name

log = logging.getLogger(__name__)

EXPIRE_EVERY_S = 30.0
PURGE_EVERY_S = 6 * 3600.0
DRAIN_TIMEOUT_S = 10.0


async def seed_if_empty(store: Store) -> bool:
    """First run: start from the editable starter rules. Returns True if it seeded."""
    if await store.list_rules():
        return False
    for text, ceiling in STARTER_RULES:
        await store.add_rule(text, ceiling, "community")
    return True


def _env_primary(env: Mapping[str, str]) -> ProviderConfig:
    name = env.get("MOTWI_AI_PROVIDER", "gemini")
    if name not in PROVIDERS:
        raise ValueError(f"MOTWI_AI_PROVIDER must be one of {', '.join(PROVIDERS)}")
    model = env.get("MOTWI_AI_MODEL") or PROVIDERS[name].default_model
    if not model:
        raise ValueError("MOTWI_AI_MODEL is required for a custom provider")
    return ProviderConfig(name, model, env.get("MOTWI_AI_BASE_URL") or None)  # type: ignore[arg-type]


def ai_key(
    slot: str, cfg: ProviderConfig, env: Mapping[str, str], secrets: SecretStore
) -> str | None:
    """A provider's key: the environment first, then Credential Manager."""
    env_name = cfg.api_key_env if slot == "primary" else "MOTWI_AI_BACKUP_KEY"
    return env.get(env_name) or secrets.get(ai_key_name(slot))  # type: ignore[arg-type]


def effective_ai(
    settings: Settings, env: Mapping[str, str], secrets: SecretStore
) -> tuple[Settings, dict[ProviderConfig, str], str | None]:
    """The providers that can run, their keys, and what is missing (None if nothing).

    A provider without a key is dropped, so blocked words and the spam filter keep
    working while the AI is off.
    """
    problem: str | None = None
    keys: dict[ProviderConfig, str] = {}
    primary = settings.primary
    if primary is None:
        try:
            primary = _env_primary(env)
        except ValueError as e:
            primary, problem = None, str(e)
    if primary is not None:
        key = ai_key("primary", primary, env, secrets)
        if key:
            keys[primary] = key
        else:
            primary, problem = None, "no AI key: run `motwi set-key` or set MOTWI_AI_KEY"
    backup = settings.backup
    if backup is not None:
        backup_key = ai_key("backup", backup, env, secrets)
        if backup_key:
            keys[backup] = backup_key
        else:
            log.warning("backup AI provider has no key; running without a backup")
            backup = None
    return replace(settings, primary=primary, backup=backup), keys, problem


def resolve_ai(
    settings: Settings, env: Mapping[str, str], secrets: SecretStore
) -> tuple[Settings, dict[ProviderConfig, str]]:
    """Like effective_ai, but a missing primary is an error (the headless `motwi run`)."""
    resolved, keys, problem = effective_ai(settings, env, secrets)
    if resolved.primary is None:
        raise ValueError(problem or "no AI provider configured")
    return resolved, keys


class MotwiApp:
    """One moderated channel: engine components plus the Twitch event handlers."""

    def __init__(
        self,
        *,
        store: Store,
        live: LiveConfig,
        executor: Executor,
        clients: Callable[[ProviderConfig], LLMClient],
        self_user_ids: set[str],
        clock: Callable[[], float] = time.time,
        bus: EventBus | None = None,
        on_stream_end: Callable[[str, float], Awaitable[None]] | None = None,
    ) -> None:
        self.store = store
        self._on_stream_end = on_stream_end or store.stream_ended
        self.live = live
        self.bus = bus or EventBus()
        self.offenses = OffenseTracker()
        self._clock = clock
        self.moderation = ModerationService(
            store=store,
            executor=executor,
            bus=self.bus,
            live=live,
            offenses=self.offenses,
            clock=clock,
        )
        self.scorer = Scorer(
            live=live, clients=clients, on_usage=store.record_usage, on_state=self._ai_state
        )
        self.pipeline = Pipeline(
            live=live,
            spam=SpamFilter(live.settings.spam),
            scorer=self.scorer,
            moderation=self.moderation,
            offenses=self.offenses,
            store=store,
            self_user_ids=self_user_ids,
            clock=clock,
            on_checked=self._checked_one,
        )
        self._tasks: set[asyncio.Task] = set()
        self._checked: Counter[tuple[str, str | None]] = Counter()

    def _checked_one(self, msg: ChatMessage) -> None:
        day = time.strftime("%Y-%m-%d", time.localtime(msg.ts))
        self._checked[(day, self.live.stream.stream_id)] += 1

    async def flush_counts(self) -> None:
        counts, self._checked = self._checked, Counter()
        try:
            await self.store.add_checked(dict(counts))
        except Exception:
            self._checked.update(counts)
            raise

    def _ai_state(self, state: AIState, detail: str) -> None:
        self.bus.publish(AIStateChanged(state, detail))

    # Twitch events

    def on_event(self, sub_type: str, event: dict[str, Any], timestamp: str) -> None:
        now = parse_ts(timestamp, self._clock())
        if sub_type == "channel.chat.message":
            if self.live.settings.paused:
                return
            msg = chat_message(event, now)
            if msg is not None:
                self._spawn(self.pipeline.handle(msg), f"message {msg.id}")
            return
        stream = self.live.stream
        if sub_type == "channel.update":
            self.live.stream = replace(
                stream, title=event.get("title"), game=event.get("category_name")
            )
        elif sub_type == "stream.online":
            stream_id = event.get("id")
            if stream.stream_id and stream.stream_id != stream_id:
                # the old stream's offline event never came
                self._spawn(self._on_stream_end(stream.stream_id, now), "stream end")
            self.live.stream = replace(stream, stream_id=stream_id)
            self.pipeline.reset_stream()
            if stream_id:
                started = parse_ts(event.get("started_at") or timestamp, now)
                self._spawn(
                    self.store.stream_started(stream_id, started, stream.title, stream.game),
                    "stream start",
                )
        elif sub_type == "stream.offline":
            self.live.stream = replace(stream, stream_id=None)
            self.pipeline.reset_stream()  # offenses reset when the stream ends (spec 5.3)
            if stream.stream_id:
                self._spawn(self._on_stream_end(stream.stream_id, now), "stream end")
        else:
            return
        self.bus.publish(StreamChanged(self.live.stream))

    def _spawn(self, coro: Awaitable[Any], what: str) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)

        def done(t: asyncio.Task) -> None:
            self._tasks.discard(t)
            if not t.cancelled() and t.exception() is not None:
                log.error("handling %s failed", what, exc_info=t.exception())

        task.add_done_callback(done)

    async def drain(self, timeout: float = DRAIN_TIMEOUT_S) -> None:
        """Wait for in-flight messages to finish."""
        if self._tasks:
            await asyncio.wait(set(self._tasks), timeout=timeout)

    # housekeeping

    async def housekeeping(
        self, *, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    ) -> None:
        last_purge: float | None = None
        while True:
            try:
                await self.flush_counts()
                await self.moderation.expire_overdue()
                now = self._clock()
                if last_purge is None or now - last_purge >= PURGE_EVERY_S:
                    days = self.live.settings.retention_days
                    removed = await self.store.purge(now - days * 86400)
                    if removed:
                        log.info("retention: removed %d old records", removed)
                    last_purge = now
            except Exception:
                log.exception("housekeeping failed")
            await sleep(EXPIRE_EVERY_S)

    async def aclose(self) -> None:
        await self.drain()
        await self.scorer.aclose()
        try:
            await self.flush_counts()
        except Exception:
            log.exception("could not save checked-message counts")


async def print_events(bus: EventBus, out: TextIO) -> None:
    """Headless console view: one line per action change, AI state and ban reminder."""
    q = bus.subscribe()
    try:
        while True:
            e = await q.get()
            if isinstance(e, ActionChanged):
                a = e.action
                actor = f" by {a.actor}" if a.actor else ""
                error = f" ({a.error})" if a.error else ""
                print(f"#{a.id} {format_action(a)} -> {a.status.value}{actor}{error}", file=out)
            elif isinstance(e, AIStateChanged):
                print(f"[ai {e.state}] {e.detail}", file=out)
            elif isinstance(e, RecapReady):
                print(f"[recap] {recap_summary(e.recap)}", file=out)
            elif isinstance(e, BanRequestReminder):
                print(
                    f"[reminder] ban request #{e.action.id} for {e.action.user_login} expired "
                    "unanswered; the hold has ended",
                    file=out,
                )
            out.flush()
    finally:
        bus.unsubscribe(q)


def stream_context(channel: dict | None, stream: dict | None) -> StreamContext:
    return StreamContext(
        title=(channel or {}).get("title"),
        game=(channel or {}).get("game_name"),
        stream_id=(stream or {}).get("id"),
    )


def ai_clients(keys: dict[ProviderConfig, str], http: httpx.AsyncClient):
    def make(cfg: ProviderConfig) -> LLMClient:
        return client_for(cfg, keys[cfg], http=http)

    return make
