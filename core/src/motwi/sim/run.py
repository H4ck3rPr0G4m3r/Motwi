"""Replays a recorded chat script through the real pipeline with a fake Twitch."""

import asyncio
import json
import os
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TextIO

from motwi.ai.providers import PROVIDERS, client_for
from motwi.ai.scorer import Scorer
from motwi.engine.offenses import OffenseTracker
from motwi.events import EventBus
from motwi.filters.spam import SpamFilter
from motwi.live import LiveConfig
from motwi.models import Ceiling, ChatMessage, Kind, Status, StreamContext
from motwi.moderation import ModerationService
from motwi.pipeline import Pipeline
from motwi.settings import ProviderConfig, Settings
from motwi.sim.fake_llm import FakeLLMClient
from motwi.sim.printer import PrintingExecutor, captured_calls, format_action, format_usage
from motwi.starter import STARTER_RULES
from motwi.store import ActionRecord, Store

STREAM_RULE = "No spoilers for Elden Ring Nightreign"
BLOCKED_PHRASES = ("strmboost", "viewbot")
FAKE_PROVIDER = ProviderConfig("custom", "fake-llm", base_url="http://fake.invalid")
_FAKE_BATCH_WAIT_S = 0.2


@dataclass
class SimSummary:
    counts: Counter[Kind] = field(default_factory=Counter)
    statuses: Counter[Status] = field(default_factory=Counter)
    unchecked: int = 0
    executor_calls: int = 0


def _env_provider(env: Mapping[str, str]) -> tuple[ProviderConfig, str]:
    key = env.get("MOTWI_AI_KEY")
    if not key:
        raise ValueError(
            "--ai env needs MOTWI_AI_KEY (and optionally MOTWI_AI_PROVIDER, "
            "MOTWI_AI_MODEL, MOTWI_AI_BASE_URL)"
        )
    provider = env.get("MOTWI_AI_PROVIDER", "gemini")
    if provider not in PROVIDERS:
        raise ValueError(f"MOTWI_AI_PROVIDER must be one of {', '.join(PROVIDERS)}")
    model = env.get("MOTWI_AI_MODEL") or PROVIDERS[provider].default_model
    if not model:
        raise ValueError("MOTWI_AI_MODEL is required for this provider")
    cfg = ProviderConfig(provider, model, env.get("MOTWI_AI_BASE_URL") or None)  # type: ignore[arg-type]
    return cfg, key


def _load_script(path: Path) -> list[dict]:
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines]


async def _seed(store: Store, settings: Settings) -> None:
    await store.save_settings(settings)
    for text, ceiling in STARTER_RULES:
        await store.add_rule(text, ceiling, "community")
    await store.add_rule(STREAM_RULE, Ceiling.DELETE, "stream")
    for phrase in BLOCKED_PHRASES:
        await store.add_blocked(phrase)


async def simulate(
    path: Path,
    *,
    ai: Literal["fake", "env"],
    speed: float,
    suggest_only: bool,
    out: TextIO,
) -> SimSummary:
    script = _load_script(path)

    if ai == "fake":
        provider, key = FAKE_PROVIDER, ""
        fake = FakeLLMClient()
        make_client = lambda cfg: fake
    else:
        provider, key = _env_provider(os.environ)
        make_client = lambda cfg: client_for(cfg, key)

    settings = Settings(
        suggest_only=suggest_only,
        primary=provider,
        batch_wait_s=_FAKE_BATCH_WAIT_S if speed <= 0 else Settings().batch_wait_s,
    )
    store = await Store.open(":memory:")
    scorer: Scorer | None = None
    try:
        await _seed(store, settings)
        live = await LiveConfig.load(store)
        live.stream = StreamContext(
            title="Nightreign blind run", game="Elden Ring Nightreign", stream_id="sim"
        )
        executor = PrintingExecutor(out)
        offenses = OffenseTracker()
        moderation = ModerationService(
            store=store, executor=executor, bus=EventBus(), live=live, offenses=offenses
        )
        scorer = Scorer(
            live=live,
            clients=make_client,
            on_usage=store.record_usage,
            on_state=lambda state, detail: print(f"[ai {state}] {detail}", file=out),
        )
        pipeline = Pipeline(
            live=live,
            spam=SpamFilter(live.settings.spam),
            scorer=scorer,
            moderation=moderation,
            offenses=offenses,
            store=store,
            self_user_ids=set(),
        )

        final: dict[int, ActionRecord] = {}

        async def handle(msg: ChatMessage) -> None:
            calls: list[str] = []
            captured_calls.set(calls)  # task-local: each message groups its own executor calls
            rec = await pipeline.handle(msg)
            if rec is None:
                return
            if rec.id in final:
                line = f"    + added to pending ban request #{rec.id}: {rec.context[-1][:60]}"
            else:
                line = format_action(rec)
            final[rec.id] = rec
            for text in (line, *calls):
                print(text, file=out)

        tasks: list[asyncio.Task] = []
        previous_t = 0.0
        for n, line in enumerate(script, start=1):
            t = float(line["t"])
            if speed > 0 and t > previous_t:
                await asyncio.sleep((t - previous_t) / speed)
            previous_t = max(previous_t, t)
            text = line["text"]
            user = line["user"]
            msg = ChatMessage(
                id=f"sim-{n}",
                user_id=f"id-{user.lower()}",
                user_login=user.lower(),
                display_name=user,
                text=text,
                emote_free_text=line.get("emote_free_text", text),
                badges=frozenset(line.get("badges", [])),
                ts=t,
            )
            # one task per message, like the live chat feed will do
            tasks.append(asyncio.create_task(handle(msg)))
        await asyncio.gather(*tasks)

        # a ban request is merged into one record, so count final records, not events
        current = {a.id: a for a in await store.list_actions(limit=len(final) + 1)}
        summary = SimSummary(
            counts=Counter(a.kind for a in current.values()),
            statuses=Counter(a.status for a in current.values()),
            unchecked=await store.count_unchecked(),
            executor_calls=executor.calls,
        )
        print(file=out)
        print(
            "Summary: "
            + (
                ", ".join(f"{k.value} {v}" for k, v in sorted(summary.counts.items()))
                or "no actions"
            ),
            file=out,
        )
        print(f"Unchecked messages: {summary.unchecked}", file=out)
        print(format_usage(await store.usage_totals()), file=out)
        return summary
    finally:
        if scorer is not None:
            await scorer.aclose()
        await store.close()
