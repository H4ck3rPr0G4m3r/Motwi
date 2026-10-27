"""Live moderation: the M1 engine wired to a real Twitch channel."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from typing import Any, TextIO

import httpx

from motwi.ai.client import LLMClient
from motwi.ai.providers import PROVIDERS, client_for
from motwi.ai.scorer import AIState, Scorer
from motwi.engine.offenses import OffenseTracker
from motwi.events import ActionChanged, AIStateChanged, BanRequestReminder, EventBus
from motwi.filters.spam import SpamFilter
from motwi.live import LiveConfig
from motwi.models import StreamContext
from motwi.moderation import Executor, ModerationService
from motwi.pipeline import Pipeline
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


def resolve_ai(
    settings: Settings, env: Mapping[str, str], secrets: SecretStore
) -> tuple[Settings, dict[ProviderConfig, str]]:
    """Pick providers and keys. Keys come from env vars first, then the credential store."""
    primary = settings.primary
    if primary is None:
        name = env.get("MOTWI_AI_PROVIDER", "gemini")
        if name not in PROVIDERS:
            raise ValueError(f"MOTWI_AI_PROVIDER must be one of {', '.join(PROVIDERS)}")
        model = env.get("MOTWI_AI_MODEL") or PROVIDERS[name].default_model
        if not model:
            raise ValueError("MOTWI_AI_MODEL is required for a custom provider")
        primary = ProviderConfig(name, model, env.get("MOTWI_AI_BASE_URL") or None)  # type: ignore[arg-type]
    key = env.get(primary.api_key_env) or secrets.get(ai_key_name("primary"))
    if not key:
        raise ValueError("no AI key: run `motwi set-key` or set MOTWI_AI_KEY")
    keys = {primary: key}
    backup = settings.backup
    if backup is not None:
        backup_key = secrets.get(ai_key_name("backup")) or env.get("MOTWI_AI_BACKUP_KEY")
        if backup_key:
            keys[backup] = backup_key
        else:
            log.warning("backup AI provider has no key; running without a backup")
            backup = None
    return replace(settings, primary=primary, backup=backup), keys


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
    ) -> None:
        self.store = store
        self.live = live
        self.bus = EventBus()
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
        )
        self._tasks: set[asyncio.Task] = set()

    def _ai_state(self, state: AIState, detail: str) -> None:
        self.bus.publish(AIStateChanged(state, detail))

    # Twitch events

    def on_event(self, sub_type: str, event: dict[str, Any], timestamp: str) -> None:
        if sub_type == "channel.chat.message":
            msg = chat_message(event, parse_ts(timestamp, self._clock()))
            if msg is not None:
                self._spawn(self.pipeline.handle(msg), f"message {msg.id}")
        elif sub_type == "channel.update":
            self.live.stream = replace(
                self.live.stream, title=event.get("title"), game=event.get("category_name")
            )
        elif sub_type == "stream.online":
            self.live.stream = replace(self.live.stream, stream_id=event.get("id"))
            self.pipeline.reset_stream()
        elif sub_type == "stream.offline":
            self.live.stream = replace(self.live.stream, stream_id=None)
            self.pipeline.reset_stream()  # offenses reset when the stream ends (spec 5.3)

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
