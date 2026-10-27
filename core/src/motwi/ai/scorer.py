"""Batched AI scoring with failover, a circuit breaker and overload protection.

Contract: every call to ``Scorer.score`` resolves to either a ``Verdict`` or an
``Unchecked`` carrying the reason. Nothing is dropped silently.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from motwi.ai.client import LLMClient, LLMError
from motwi.ai.parse import ParseError, parse_verdicts
from motwi.ai.prompt import BatchItem, BuiltRequest, build_request
from motwi.live import LiveConfig
from motwi.models import ChatMessage, Unchecked, Verdict
from motwi.settings import ProviderConfig
from motwi.store import Usage

log = logging.getLogger(__name__)

AIState = Literal["ok", "degraded", "down", "off"]
Result = Verdict | Unchecked

ATTEMPTS_PER_PROVIDER = 2


@dataclass
class _Pending:
    item: BatchItem
    context: Sequence[ChatMessage]
    future: "asyncio.Future[Result]"
    enqueued_at: float  # event-loop time


class Scorer:
    def __init__(
        self,
        *,
        live: LiveConfig,
        clients: Callable[[ProviderConfig], LLMClient],
        on_usage: Callable[[Usage], Awaitable[None]],
        on_state: Callable[[AIState, str], None],
        max_in_flight: int = 4,
        max_pending: int = 200,
        breaker_failures: int = 3,
        breaker_cooldown_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._live = live
        self._clients = clients
        self._on_usage = on_usage
        self._on_state = on_state
        self._max_pending = max_pending
        self._breaker_failures = breaker_failures
        self._breaker_cooldown_s = breaker_cooldown_s
        self._clock = clock
        self._sem = asyncio.Semaphore(max_in_flight)
        self._queue: list[_Pending] = []
        self._wake = asyncio.Event()
        self._flusher: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()
        self._client_cache: dict[ProviderConfig, LLMClient] = {}
        self._primary_failures = 0
        self._primary_open_until: float | None = None
        self._state: AIState = "ok"
        self._closed = False

    @property
    def state(self) -> AIState:
        return self._state

    async def score(self, item: BatchItem, context: Sequence[ChatMessage]) -> Result:
        msg_id = item.message.id
        if self._closed:
            return Unchecked(msg_id, "shutdown")
        settings = self._live.settings
        if settings.primary is None and settings.backup is None:
            self._set_state("off", "no AI provider configured")
            return Unchecked(msg_id, "ai_off")
        if len(self._queue) + 1 > self._max_pending:
            return Unchecked(msg_id, "overload")

        loop = asyncio.get_running_loop()
        future: asyncio.Future[Result] = loop.create_future()
        self._queue.append(_Pending(item, context, future, loop.time()))
        if self._flusher is None:
            self._flusher = loop.create_task(self._flush_loop())
        self._wake.set()
        return await future

    async def aclose(self) -> None:
        self._closed = True
        tasks = [t for t in (self._flusher, *self._tasks) if t is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._flusher = None
        for pending in self._queue:
            self._resolve(pending, Unchecked(pending.item.message.id, "shutdown"))
        self._queue.clear()
        for client in self._client_cache.values():
            close = getattr(client, "aclose", None)
            if close is not None:
                await close()

    # batching

    async def _flush_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            if not self._queue:
                self._wake.clear()
                await self._wake.wait()
                continue
            settings = self._live.settings
            while len(self._queue) < settings.batch_max:
                remaining = self._queue[0].enqueued_at + settings.batch_wait_s - loop.time()
                if remaining <= 0:
                    break
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), remaining)
                except TimeoutError:
                    break
            # Take the slot before taking items so a saturated scorer keeps them queued,
            # where they count toward max_pending.
            await self._sem.acquire()
            size = max(1, self._live.settings.batch_max)
            batch, self._queue = self._queue[:size], self._queue[size:]
            task = loop.create_task(self._run_batch(batch))
            self._tasks.add(task)
            task.add_done_callback(self._batch_done)

    def _batch_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        self._sem.release()

    @staticmethod
    def _resolve(pending: _Pending, result: Result) -> None:
        if not pending.future.done():
            pending.future.set_result(result)

    async def _run_batch(self, batch: list[_Pending]) -> None:
        try:
            results = await self._score_batch(batch)
        except asyncio.CancelledError:
            for pending in batch:
                self._resolve(pending, Unchecked(pending.item.message.id, "shutdown"))
            raise
        except Exception:
            log.exception("unexpected scorer failure")
            results = {}
        for pending in batch:
            msg_id = pending.item.message.id
            self._resolve(pending, results.get(msg_id) or Unchecked(msg_id, "ai_unavailable"))

    # failover

    async def _score_batch(self, batch: list[_Pending]) -> dict[str, Result]:
        settings = self._live.settings
        req = build_request(
            [p.item for p in batch],
            context=batch[0].context,
            rules=self._live.rules,
            stream=self._live.stream,
        )

        verdicts: dict[str, Verdict] | None = None
        failure = ""
        if settings.primary is not None:
            if self._breaker_open():
                failure = "primary skipped (circuit breaker open)"
            else:
                verdicts, failure = await self._try_provider(settings.primary, req, batch)
                if verdicts is None:
                    self._primary_failed()
                else:
                    self._primary_failures = 0
                    self._primary_open_until = None
                    self._set_state("ok", f"{settings.primary.provider} responding")

        if verdicts is None and settings.backup is not None:
            verdicts, backup_failure = await self._try_provider(settings.backup, req, batch)
            if verdicts is not None:
                reason = f"using backup {settings.backup.provider}"
                self._set_state("degraded", f"{failure}; {reason}" if failure else reason)
            else:
                failure = f"{failure}; {backup_failure}" if failure else backup_failure

        if verdicts is None:
            self._set_state("down", failure or "AI unavailable")
            return {}

        return {
            p.item.message.id: verdicts.get(p.item.message.id)
            or Unchecked(p.item.message.id, "missing_from_response")
            for p in batch
        }

    async def _try_provider(
        self, cfg: ProviderConfig, req: BuiltRequest, batch: list[_Pending]
    ) -> tuple[dict[str, Verdict] | None, str]:
        error = ""
        for _ in range(ATTEMPTS_PER_PROVIDER):
            try:
                client = self._client(cfg)
                completion = await client.complete(
                    req.messages, timeout_s=self._live.settings.ai_timeout_s
                )
            except LLMError as e:
                error = str(e)
                continue
            except Exception as e:
                log.exception("could not call %s", cfg.provider)
                error = f"{cfg.provider}: {type(e).__name__}"
                continue

            await self._report_usage(completion, len(batch))
            try:
                return parse_verdicts(completion.text, req), ""
            except ParseError as e:
                error = f"{cfg.provider}: unparseable reply ({e})"
        return None, error

    def _client(self, cfg: ProviderConfig) -> LLMClient:
        client = self._client_cache.get(cfg)
        if client is None:
            client = self._client_cache[cfg] = self._clients(cfg)
        return client

    async def _report_usage(self, completion, messages: int) -> None:
        usage = Usage(
            ts=time.time(),
            provider=completion.provider,
            model=completion.model,
            prompt_tokens=completion.prompt_tokens,
            completion_tokens=completion.completion_tokens,
            cost_usd=completion.cost_usd,
            messages=messages,
            stream_id=self._live.stream.stream_id,
        )
        try:
            await self._on_usage(usage)
        except Exception:
            log.exception("usage callback failed")

    # circuit breaker and state

    def _breaker_open(self) -> bool:
        return self._primary_open_until is not None and self._clock() < self._primary_open_until

    def _primary_failed(self) -> None:
        self._primary_failures += 1
        if self._primary_failures >= self._breaker_failures:
            self._primary_open_until = self._clock() + self._breaker_cooldown_s

    def _set_state(self, state: AIState, detail: str) -> None:
        if state == self._state:
            return
        self._state = state
        try:
            self._on_state(state, detail)
        except Exception:
            log.exception("state callback failed")
