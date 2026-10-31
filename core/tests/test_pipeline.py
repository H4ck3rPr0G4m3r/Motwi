import asyncio
import json
from itertools import count

import pytest
from conftest import RecordingExecutor

from motwi.ai.client import Completion, LLMError
from motwi.ai.scorer import Scorer
from motwi.engine.offenses import OffenseTracker
from motwi.events import EventBus
from motwi.filters.blocked import BlockedWord, BlockedWords
from motwi.filters.spam import SpamFilter
from motwi.live import LiveConfig
from motwi.models import Ceiling, ChatMessage, Kind, Rule, Source, Status, StreamContext
from motwi.moderation import ModerationService
from motwi.pipeline import Pipeline
from motwi.settings import ProviderConfig, Settings

PRIMARY = ProviderConfig("gemini", "fake-model")
_ids = count(1)


def chat(text="hello there", *, login="viewer1", user_id=None, badges=(), ts=None) -> ChatMessage:
    n = next(_ids)
    return ChatMessage(
        f"m{n}",
        user_id or f"id-{login}",
        login,
        login.title(),
        text,
        text,
        frozenset(badges),
        float(n) if ts is None else ts,
    )


class FakeAI:
    """Scores by message text: {text: (score, rule_id)}; anything else scores 1.0."""

    provider = "gemini"
    model = "fake-model"

    def __init__(self, scores=None, error: Exception | None = None) -> None:
        self.scores = scores or {}
        self.error = error
        self.calls: list[dict] = []

    async def complete(self, messages, *, timeout_s):
        if self.error is not None:
            raise self.error
        body = json.loads(messages[1]["content"])
        self.calls.append(body)
        results = []
        for m in body["messages"]:
            score, rule = self.scores.get(m["text"], (1.0, None))
            results.append({"id": m["id"], "score": score, "rule": rule, "reason": "because"})
        return Completion(json.dumps({"results": results}), 10, 5, 0.0, self.provider, self.model)


class Rig:
    def __init__(self, store, ai: FakeAI, *, rules=(), blocked=(), settings=None, **sets) -> None:
        base = settings or Settings(batch_wait_s=0.02, ai_timeout_s=1.0)
        self.live = LiveConfig(
            settings=Settings.from_dict({**base.to_dict(), "primary": PRIMARY.__dict__, **sets}),
            rules=list(rules),
            blocked=BlockedWords(blocked),
            trusted=set(),
            vips=set(),
            stream=StreamContext(stream_id="s1"),
        )
        self.ai = ai
        self.store = store
        self.executor = RecordingExecutor()
        self.offenses = OffenseTracker()
        self.spam = SpamFilter(self.live.settings.spam)
        self.scorer = Scorer(
            live=self.live,
            clients=lambda cfg: ai,
            on_usage=self._noop,
            on_state=lambda state, detail: None,
        )
        self.moderation = ModerationService(
            store=store,
            executor=self.executor,
            bus=EventBus(),
            live=self.live,
            offenses=self.offenses,
        )
        self.pipeline = Pipeline(
            live=self.live,
            spam=self.spam,
            scorer=self.scorer,
            moderation=self.moderation,
            offenses=self.offenses,
            store=store,
            self_user_ids={"bot-id"},
        )

    @staticmethod
    async def _noop(usage) -> None:
        return None


@pytest.fixture
async def make_rig(store):
    rigs: list[Rig] = []

    def factory(ai=None, **kw) -> Rig:
        rig = Rig(store, ai or FakeAI(), **kw)
        rigs.append(rig)
        return rig

    yield factory
    for rig in rigs:
        await rig.scorer.aclose()


BAD = BlockedWord("b1", "badword")


async def test_mod_and_broadcaster_and_trusted_exempt(make_rig):
    rig = make_rig(blocked=[BAD], ai=FakeAI({"badword here": (9.5, None)}))
    rig.live.trusted.add("friend")
    for m in (
        chat("badword here", login="themod", badges=["moderator"]),
        chat("badword here", login="boss", badges=["broadcaster"]),
        chat("badword here", login="friend"),
        chat("badword here", login="bot", user_id="bot-id"),
    ):
        assert await rig.pipeline.handle(m) is None
    assert rig.executor.calls == []
    assert rig.ai.calls == []


async def test_blocked_word_acts_without_ai(make_rig):
    rig = make_rig(blocked=[BAD])
    m = chat("you badword person")
    rec = await rig.pipeline.handle(m)
    assert rec is not None
    assert rec.source is Source.BLOCKED_WORD
    assert rec.kind is Kind.DELETE
    assert rec.status is Status.DONE
    assert rec.reason == "Blocked word: badword"
    assert rig.executor.calls == [("delete", m.id)]
    assert rig.ai.calls == []


async def test_spam_acts_without_ai(make_rig):
    rig = make_rig()
    m = chat("a" * 80)
    rec = await rig.pipeline.handle(m)
    assert rec is not None
    assert rec.source is Source.SPAM
    assert rec.status is Status.DONE
    assert rec.reason == "Spam filter: character flood"
    assert rig.executor.calls == [("delete", m.id)]
    assert rig.ai.calls == []


async def test_borderline_sends_recent_history_to_ai(make_rig):
    rig = make_rig()
    text = "check out strmboost.shop"
    assert await rig.pipeline.handle(chat(text)) is None
    sent = rig.ai.calls[0]["messages"][0]
    assert sent["recent_from_user"] == [text]


async def test_clean_message_carries_no_recent_history(make_rig):
    rig = make_rig()
    await rig.pipeline.handle(chat("gg well played"))
    assert "recent_from_user" not in rig.ai.calls[0]["messages"][0]


async def test_ai_gets_preceding_context_lines(make_rig):
    rig = make_rig(context_lines=2)
    for text in ("one", "two", "three"):
        await rig.pipeline.handle(chat(text, login="other"))
    await rig.pipeline.handle(chat("four"))
    context = rig.ai.calls[-1]["recent_chat"]
    assert [c["text"] for c in context] == ["two", "three"]


async def test_ai_verdict_flows_to_action(make_rig):
    rule = Rule("rule-spoilers", "No spoilers", Ceiling.DELETE, "community")
    rig = make_rig(ai=FakeAI({"he dies at the end": (8.1, "r1")}), rules=[rule])
    await rig.pipeline.handle(chat("earlier line", login="other"))
    m = chat("he dies at the end")
    rec = await rig.pipeline.handle(m)
    assert rec is not None
    assert rec.kind is Kind.DELETE
    assert rec.status is Status.DONE
    assert rec.rule_id == "rule-spoilers"
    assert rec.score == 8.1
    assert rec.reason == "because"
    assert rec.stream_id == "s1"
    assert rec.context == ["other: earlier line"]
    assert rig.executor.calls == [("delete", m.id)]


async def test_recorded_context_is_last_five_lines(make_rig):
    rig = make_rig(ai=FakeAI({"rude": (7.0, None)}))
    for i in range(8):
        await rig.pipeline.handle(chat(f"line {i}", login="other"))
    rec = await rig.pipeline.handle(chat("rude"))
    assert rec.context == [f"other: line {i}" for i in range(3, 8)]


async def test_low_score_takes_no_action(make_rig):
    rig = make_rig()
    assert await rig.pipeline.handle(chat("lovely stream")) is None
    assert rig.executor.calls == []


async def test_unchecked_is_recorded(make_rig, store):
    rig = make_rig(ai=FakeAI(error=LLMError("down")))
    assert await rig.pipeline.handle(chat("anything")) is None
    assert await store.count_unchecked() == 1
    assert rig.executor.calls == []


async def test_vip_gets_leeway(make_rig):
    rig = make_rig(ai=FakeAI({"mildly rude": (6.5, None)}))  # standard warn 6.0, +1.0 for VIPs
    rig.live.vips.add("vipguy")
    assert await rig.pipeline.handle(chat("mildly rude", login="vipguy")) is None
    assert await rig.pipeline.handle(chat("mildly rude", login="badge", badges=["vip"])) is None
    rec = await rig.pipeline.handle(chat("mildly rude", login="regular"))
    assert rec is not None and rec.kind is Kind.WARN


async def test_escalation_across_messages(make_rig):
    rule = Rule("rule-ban", "No harassment", Ceiling.BAN, "community")
    texts = ["harass one", "harass two", "harass three"]
    rig = make_rig(ai=FakeAI({t: (6.2, "r1") for t in texts}), rules=[rule])
    records = [await rig.pipeline.handle(chat(t)) for t in texts]
    assert [(r.kind, r.duration_s) for r in records] == [
        (Kind.WARN, None),
        (Kind.TIMEOUT, 60),
        (Kind.TIMEOUT, 600),
    ]


async def test_reset_stream_clears_offenses(make_rig):
    rig = make_rig(ai=FakeAI({"rude one": (6.5, None), "rude two": (6.5, None)}))
    first = await rig.pipeline.handle(chat("rude one"))
    assert first.kind is Kind.WARN
    rig.pipeline.reset_stream()
    again = await rig.pipeline.handle(chat("rude two"))
    assert again.kind is Kind.WARN


async def test_reset_stream_forgets_known_chatters_and_context(make_rig):
    rig = make_rig()
    link = "check out strmboost.shop"
    await rig.pipeline.handle(chat("hi everyone"))
    await rig.pipeline.handle(chat(link))  # known chatter: link is not borderline
    assert "recent_from_user" not in rig.ai.calls[-1]["messages"][0]
    rig.pipeline.reset_stream()
    await rig.pipeline.handle(chat(link))  # new chatter again
    last = rig.ai.calls[-1]
    assert "recent_from_user" in last["messages"][0]
    assert last["recent_chat"] == []


async def test_reset_stream_keeps_locks_held_by_in_flight_messages(make_rig):
    rig = make_rig()
    held = rig.pipeline._user_locks.setdefault("id-busy", asyncio.Lock())
    rig.pipeline._user_locks.setdefault("id-idle", asyncio.Lock())
    await held.acquire()
    rig.pipeline.reset_stream()
    assert rig.pipeline._user_locks == {"id-busy": held}
    held.release()


async def test_rule_set_to_ban_asks_for_a_ban_on_the_first_offense(make_rig):
    rule = Rule("rule-slurs", "No slurs", Ceiling.ALWAYS_BAN, "community")
    rig = make_rig(ai=FakeAI({"slur": (6.2, "r1")}), rules=[rule])
    rec = await rig.pipeline.handle(chat("slur"))
    assert rec.kind is Kind.BAN_REQUEST
    assert rec.status is Status.PENDING
    assert not any(call[0] == "ban" for call in rig.executor.calls)


async def test_rule_set_to_timeout_times_out_on_the_first_offense(make_rig):
    rule = Rule("rule-insults", "No insults", Ceiling.ALWAYS_TIMEOUT, "community")
    rig = make_rig(ai=FakeAI({"insult": (6.2, "r1")}), rules=[rule])
    rec = await rig.pipeline.handle(chat("insult"))
    assert (rec.kind, rec.duration_s, rec.status) == (Kind.TIMEOUT, 60, Status.DONE)
