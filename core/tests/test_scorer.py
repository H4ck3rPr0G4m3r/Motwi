"""Scorer tests. Synchronisation uses events and futures; batch waits are tiny."""

import asyncio
import json
import time

import pytest

from motwi.ai.client import Completion, LLMError
from motwi.ai.prompt import BatchItem
from motwi.ai.scorer import Scorer
from motwi.filters.blocked import BlockedWords
from motwi.live import LiveConfig
from motwi.models import ChatMessage, StreamContext, Unchecked, Verdict
from motwi.settings import ProviderConfig, Settings

PRIMARY = ProviderConfig("gemini", "primary-model")
BACKUP = ProviderConfig("openrouter", "backup-model")
TIMEOUT = 5.0  # safety net only; healthy tests finish in milliseconds


def chat(mid: str) -> ChatMessage:
    return ChatMessage(mid, f"u-{mid}", f"user_{mid}", mid, "hello", "hello", frozenset(), 0.0)


def item(mid: str) -> BatchItem:
    return BatchItem(chat(mid))


def ids_in(messages: list[dict]) -> list[str]:
    return [m["id"] for m in json.loads(messages[1]["content"])["messages"]]


def reply(messages: list[dict], score: float = 1.0, only: int | None = None) -> str:
    ids = ids_in(messages)[:only]
    results = [{"id": i, "score": score, "rule": None, "reason": "ok"} for i in ids]
    return json.dumps({"results": results})


class FakeClient:
    """Each complete() call consumes the next script entry (or repeats `default`).

    An entry is an Exception (raised), a str (returned as reply text) or a callable
    taking the request messages and returning reply text.
    """

    def __init__(self, provider: str, script=None, default=None, gate: asyncio.Event | None = None):
        self.provider = provider
        self.model = f"{provider}-model"
        self.script = list(script or [])
        self.default = default
        self.gate = gate
        self.calls: list[list[dict]] = []
        self.started = asyncio.Event()

    async def complete(self, messages, *, timeout_s):
        self.calls.append(messages)
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        entry = self.script.pop(0) if self.script else self.default
        if isinstance(entry, Exception):
            raise entry
        text = entry(messages) if callable(entry) else entry
        return Completion(text, 100, 20, 0.001, self.provider, self.model)


class Harness:
    def __init__(self, *, primary=None, backup=None, settings=None, clock=None, **scorer_kw):
        self.primary = primary
        self.backup = backup
        by_cfg = {PRIMARY: primary, BACKUP: backup}
        base = settings or Settings(batch_max=8, batch_wait_s=0.02)
        self.live = LiveConfig(
            settings=Settings.from_dict(
                {
                    **base.to_dict(),
                    "primary": PRIMARY.__dict__ if primary else None,
                    "backup": BACKUP.__dict__ if backup else None,
                }
            ),
            rules=[],
            blocked=BlockedWords([]),
            trusted=set(),
            vips=set(),
            stream=StreamContext(stream_id="stream-1"),
        )
        self.usage = []
        self.states: list[str] = []

        async def on_usage(u):
            self.usage.append(u)

        extra = {"clock": clock} if clock else {}
        self.scorer = Scorer(
            live=self.live,
            clients=lambda cfg: by_cfg[cfg],
            on_usage=on_usage,
            on_state=lambda s, detail: self.states.append(s),
            **extra,
            **scorer_kw,
        )

    async def score_all(self, *ids: str):
        return await asyncio.wait_for(
            asyncio.gather(*(self.scorer.score(item(i), []) for i in ids)), TIMEOUT
        )


@pytest.fixture
async def make():
    made: list[Harness] = []

    def factory(**kw) -> Harness:
        h = Harness(**kw)
        made.append(h)
        return h

    yield factory
    for h in made:
        await h.scorer.aclose()


async def test_batches_by_size(make):
    # A long wait proves the flush was triggered by size, not by the timer.
    primary = FakeClient("gemini", default=reply)
    h = make(primary=primary, settings=Settings(batch_max=8, batch_wait_s=5.0))
    started = time.monotonic()
    results = await h.score_all(*[f"id{i}" for i in range(8)])
    assert time.monotonic() - started < 2.0
    assert len(primary.calls) == 1
    assert [r.message_id for r in results] == [f"id{i}" for i in range(8)]
    assert all(isinstance(r, Verdict) for r in results)
    assert h.scorer.state == "ok" and h.states == []


async def test_flushes_after_wait(make):
    primary = FakeClient("gemini", default=reply)
    h = make(primary=primary, settings=Settings(batch_max=8, batch_wait_s=0.2))
    started = time.monotonic()
    results = await h.score_all("a", "b")
    assert len(primary.calls) == 1 and len(results) == 2
    assert 0.15 <= time.monotonic() - started < 1.5


async def test_more_than_batch_max_splits_into_batches(make):
    primary = FakeClient("gemini", default=reply)
    h = make(primary=primary, settings=Settings(batch_max=3, batch_wait_s=0.02))
    results = await h.score_all(*[f"id{i}" for i in range(7)])
    assert all(isinstance(r, Verdict) for r in results)
    assert sorted(len(ids_in(c)) for c in primary.calls) == [1, 3, 3]


async def test_batch_context_comes_from_first_item(make):
    primary = FakeClient("gemini", default=reply)
    h = make(primary=primary, settings=Settings(batch_max=2, batch_wait_s=5.0))
    first_ctx, second_ctx = [chat("ctx1")], [chat("ctx2")]
    await asyncio.wait_for(
        asyncio.gather(h.scorer.score(item("a"), first_ctx), h.scorer.score(item("b"), second_ctx)),
        TIMEOUT,
    )
    body = json.loads(primary.calls[0][1]["content"])
    assert [m["user"] for m in body["recent_chat"]] == ["user_ctx1"]


async def test_fails_over_to_backup_and_reports_degraded(make):
    primary = FakeClient("gemini", default=LLMError("down"))
    backup = FakeClient("openrouter", default=reply)
    h = make(primary=primary, backup=backup)
    results = await h.score_all("a", "b")
    assert all(isinstance(r, Verdict) for r in results)
    assert len(primary.calls) == 2 and len(backup.calls) == 1
    assert h.states == ["degraded"] and h.scorer.state == "degraded"
    assert h.usage[0].provider == "openrouter"


async def test_recovers_to_ok_when_primary_succeeds_again(make):
    primary = FakeClient("gemini", script=[LLMError("x"), LLMError("x")], default=reply)
    backup = FakeClient("openrouter", default=reply)
    h = make(primary=primary, backup=backup)
    await h.score_all("a")
    await h.score_all("b")
    assert h.states == ["degraded", "ok"]


async def test_both_down_marks_unchecked_and_state_down(make):
    primary = FakeClient("gemini", default=LLMError("down"))
    backup = FakeClient("openrouter", default=LLMError("down too"))
    h = make(primary=primary, backup=backup)
    results = await h.score_all("a", "b")
    assert results == [Unchecked("a", "ai_unavailable"), Unchecked("b", "ai_unavailable")]
    assert len(primary.calls) == 2 and len(backup.calls) == 2
    assert h.states == ["down"] and h.scorer.state == "down"
    assert h.usage == []


async def test_primary_only_failure_is_down(make):
    h = make(primary=FakeClient("gemini", default=LLMError("down")))
    assert await h.score_all("a") == [Unchecked("a", "ai_unavailable")]
    assert h.states == ["down"]


async def test_parse_error_counts_as_failure_then_retry_succeeds(make):
    primary = FakeClient("gemini", script=["I think it is fine"], default=reply)
    h = make(primary=primary)
    (result,) = await h.score_all("a")
    assert isinstance(result, Verdict)
    assert len(primary.calls) == 2
    assert h.states == []
    assert len(h.usage) == 2  # the unparseable completion was still paid for


async def test_unexpected_client_exception_is_a_failed_attempt(make):
    primary = FakeClient("gemini", script=[KeyError("MOTWI_AI_KEY")], default=reply)
    h = make(primary=primary)
    (result,) = await h.score_all("a")
    assert isinstance(result, Verdict)


async def test_breaker_skips_primary_then_probes(make):
    now = [1000.0]
    primary = FakeClient("gemini", default=LLMError("down"))
    backup = FakeClient("openrouter", default=reply)
    h = make(
        primary=primary,
        backup=backup,
        clock=lambda: now[0],
        breaker_failures=3,
        breaker_cooldown_s=60.0,
    )
    for i in range(3):
        await h.score_all(f"f{i}")
    assert len(primary.calls) == 6  # 3 batches x 2 attempts

    await h.score_all("skipped")
    assert len(primary.calls) == 6  # breaker open: primary not touched
    assert len(backup.calls) == 4

    now[0] += 61.0
    await h.score_all("probe")
    assert len(primary.calls) == 8  # probed again after the cooldown
    await h.score_all("reopened")
    assert len(primary.calls) == 8  # failed probe re-opens the breaker immediately


async def test_breaker_closes_after_successful_probe(make):
    now = [0.0]
    primary = FakeClient("gemini", default=LLMError("down"))
    backup = FakeClient("openrouter", default=reply)
    h = make(
        primary=primary,
        backup=backup,
        clock=lambda: now[0],
        breaker_failures=1,
        breaker_cooldown_s=10,
    )
    await h.score_all("a")
    now[0] = 11.0
    primary.default = reply
    await h.score_all("b")
    assert h.scorer.state == "ok"
    await h.score_all("c")
    assert len(backup.calls) == 1


async def test_overload_marks_unchecked(make):
    gate = asyncio.Event()
    primary = FakeClient("gemini", default=reply, gate=gate)
    h = make(
        primary=primary,
        settings=Settings(batch_max=2, batch_wait_s=0.02),
        max_pending=5,
        max_in_flight=1,
    )
    tasks = [asyncio.ensure_future(h.scorer.score(item(f"id{i}"), [])) for i in range(12)]
    await asyncio.wait_for(primary.started.wait(), TIMEOUT)
    gate.set()
    results = await asyncio.wait_for(asyncio.gather(*tasks), TIMEOUT)
    overloaded = [r for r in results if r == Unchecked(r.message_id, "overload")]
    assert len(overloaded) >= 7
    assert len(results) == 12 and all(isinstance(r, (Verdict, Unchecked)) for r in results)
    assert sum(isinstance(r, Verdict) for r in results) == 12 - len(overloaded)


async def test_max_in_flight_limits_concurrency(make):
    gate = asyncio.Event()
    primary = FakeClient("gemini", default=reply, gate=gate)
    h = make(primary=primary, settings=Settings(batch_max=1, batch_wait_s=0.02), max_in_flight=2)
    tasks = [asyncio.ensure_future(h.scorer.score(item(f"id{i}"), [])) for i in range(5)]
    await asyncio.wait_for(primary.started.wait(), TIMEOUT)
    for _ in range(20):
        await asyncio.sleep(0)
    assert len(primary.calls) == 2  # the rest wait for a slot
    gate.set()
    results = await asyncio.wait_for(asyncio.gather(*tasks), TIMEOUT)
    assert all(isinstance(r, Verdict) for r in results)
    assert len(primary.calls) == 5


async def test_missing_ids_unchecked(make):
    primary = FakeClient("gemini", default=lambda m: reply(m, only=1))
    h = make(primary=primary)
    results = await h.score_all("a", "b")
    assert isinstance(results[0], Verdict)
    assert results[1] == Unchecked("b", "missing_from_response")
    assert h.states == []


async def test_no_provider_is_off(make):
    h = make()
    assert await h.score_all("a") == [Unchecked("a", "ai_off")]
    assert h.scorer.state == "off" and h.states == ["off"]


async def test_usage_recorded_per_batch(make):
    primary = FakeClient("gemini", default=reply)
    h = make(primary=primary, settings=Settings(batch_max=3, batch_wait_s=0.02))
    await h.score_all("a", "b", "c", "d")
    assert sorted(u.messages for u in h.usage) == [1, 3]
    usage = h.usage[0]
    assert usage.provider == "gemini" and usage.model == "gemini-model"
    assert (usage.prompt_tokens, usage.completion_tokens, usage.cost_usd) == (100, 20, 0.001)
    assert usage.stream_id == "stream-1"


async def test_failing_usage_callback_does_not_lose_verdicts():
    live = LiveConfig(
        Settings(primary=PRIMARY, batch_wait_s=0.02), [], BlockedWords([]), set(), set()
    )
    primary = FakeClient("gemini", default=reply)

    async def boom(_):
        raise RuntimeError("db closed")

    scorer = Scorer(
        live=live, clients=lambda cfg: primary, on_usage=boom, on_state=lambda s, d: None
    )
    try:
        result = await asyncio.wait_for(scorer.score(item("a"), []), TIMEOUT)
        assert isinstance(result, Verdict)
    finally:
        await scorer.aclose()


async def test_aclose_resolves_pending_items(make):
    primary = FakeClient("gemini", default=reply)
    h = make(primary=primary, settings=Settings(batch_max=8, batch_wait_s=30.0))
    task = asyncio.ensure_future(h.scorer.score(item("a"), []))
    for _ in range(5):
        await asyncio.sleep(0)
    await h.scorer.aclose()
    assert await asyncio.wait_for(task, TIMEOUT) == Unchecked("a", "shutdown")
    assert await h.scorer.score(item("b"), []) == Unchecked("b", "shutdown")
    assert primary.calls == []


async def test_aclose_resolves_in_flight_items(make):
    gate = asyncio.Event()
    primary = FakeClient("gemini", default=reply, gate=gate)
    h = make(primary=primary)
    task = asyncio.ensure_future(h.scorer.score(item("a"), []))
    await asyncio.wait_for(primary.started.wait(), TIMEOUT)
    await h.scorer.aclose()
    assert await asyncio.wait_for(task, TIMEOUT) == Unchecked("a", "shutdown")
