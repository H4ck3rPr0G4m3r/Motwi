import asyncio
import time
from dataclasses import replace

import pytest
from conftest import RecordingExecutor

from motwi.engine.decide import Plan
from motwi.engine.offenses import OffenseTracker
from motwi.events import ActionChanged, BanRequestReminder, EventBus
from motwi.filters.blocked import BlockedWords
from motwi.live import LiveConfig
from motwi.models import ChatMessage, Kind, Source, Status
from motwi.moderation import AlreadyResolved, ModerationService, Outcome
from motwi.settings import Settings


def msg(text="you are awful", mid="m1", user_id="u1", login="viewer1") -> ChatMessage:
    return ChatMessage(mid, user_id, login, login.title(), text, text, frozenset(), 1000.0)


def outcome(kind: Kind, duration_s=None, ladder_index=1, reason="insult") -> Outcome:
    return Outcome(Source.AI, Plan(kind, True, duration_s, ladder_index), 8.0, None, reason)


TIMEOUT = outcome(Kind.TIMEOUT, 60)
WARN = outcome(Kind.WARN, ladder_index=0)
BAN = outcome(Kind.BAN_REQUEST, ladder_index=3, reason="slurs")


@pytest.fixture
def ex():
    return RecordingExecutor()


@pytest.fixture
def bus():
    return EventBus()


@pytest.fixture
def offenses():
    return OffenseTracker()


@pytest.fixture
def live():
    return LiveConfig(Settings(), [], BlockedWords([]), set(), set())


@pytest.fixture
def svc(store, ex, bus, live, offenses):
    return ModerationService(store=store, executor=ex, bus=bus, live=live, offenses=offenses)


def set_settings(live, **changes):
    live.settings = replace(live.settings, **changes)


async def test_timeout_plan_deletes_then_times_out(svc, ex, offenses):
    rec = await svc.apply(msg(), TIMEOUT, ["earlier"])
    assert ex.calls == [("delete", "m1"), ("timeout", "u1", 60, "Motwi: insult")]
    assert rec.status is Status.DONE
    assert rec.context == ["earlier"]
    assert offenses.last_index("u1", time.time()) == 1


async def test_none_plan_returns_none(svc, ex):
    assert await svc.apply(msg(), outcome(Kind.NONE, ladder_index=None), []) is None
    assert ex.calls == []


async def test_executor_failure_marks_failed_no_offense(svc, ex, offenses):
    ex.fail_next = "timeout"
    rec = await svc.apply(msg(), TIMEOUT, [])
    assert rec.status is Status.FAILED
    assert rec.error == "timeout failed"
    assert offenses.last_index("u1", time.time()) is None


async def test_twitch_warn_uses_warn_api(svc, ex):
    await svc.apply(msg(), WARN, [])
    assert ex.calls == [("delete", "m1"), ("warn", "u1", "Motwi: insult")]


async def test_chat_warn_style_sends_mention(svc, ex, live):
    set_settings(live, warn_style="chat")
    rec = await svc.apply(msg(), WARN, [])
    assert ex.calls == [("delete", "m1"), ("chat", "@viewer1 Motwi: insult")]
    assert rec.status is Status.DONE


async def test_delete_only_kind_just_deletes(svc, ex, offenses):
    rec = await svc.apply(msg(), outcome(Kind.DELETE, ladder_index=-1), [])
    assert ex.calls == [("delete", "m1")]
    assert rec.status is Status.DONE
    assert offenses.last_index("u1", time.time()) == -1


async def test_suggest_only_executes_nothing_until_approved(svc, ex, live, offenses):
    set_settings(live, suggest_only=True)
    rec = await svc.apply(msg(), TIMEOUT, [])
    assert rec.status is Status.SUGGESTED
    assert ex.calls == []
    assert offenses.last_index("u1", time.time()) is None

    done = await svc.approve(rec.id, "streamer")
    assert ex.calls == [("delete", "m1"), ("timeout", "u1", 60, "Motwi (streamer): insult")]
    assert done.status is Status.DONE
    assert done.actor == "streamer"
    assert offenses.last_index("u1", time.time()) == 1


async def test_suggest_only_ban_request_approve_deletes_and_bans(svc, ex, live):
    set_settings(live, suggest_only=True)
    rec = await svc.apply(msg(), BAN, [])
    assert rec.status is Status.SUGGESTED
    done = await svc.approve(rec.id, "streamer")
    assert ex.calls == [("delete", "m1"), ("ban", "u1", "Motwi (streamer): slurs")]
    assert done.status is Status.DONE


async def test_suggest_only_approve_failure_marks_failed(svc, ex, live, offenses):
    set_settings(live, suggest_only=True)
    rec = await svc.apply(msg(), TIMEOUT, [])
    ex.fail_next = "timeout"
    done = await svc.approve(rec.id, "streamer")
    assert done.status is Status.FAILED
    assert done.error == "timeout failed"
    assert offenses.last_index("u1", time.time()) is None


async def test_suggest_only_deny_ignores(svc, ex, live):
    set_settings(live, suggest_only=True)
    rec = await svc.apply(msg(), TIMEOUT, [])
    denied = await svc.deny(rec.id, "streamer")
    assert denied.status is Status.IGNORED
    assert ex.calls == []
    with pytest.raises(AlreadyResolved):
        await svc.approve(rec.id, "streamer")


async def test_ban_request_applies_hold(svc, ex):
    rec = await svc.apply(msg(), BAN, [])
    assert rec.status is Status.PENDING
    assert rec.hold_applied is True
    assert ex.calls == [
        ("delete", "m1"),
        ("timeout", "u1", 600, "Motwi: held pending ban review"),
    ]


async def test_ban_request_without_hold_setting(svc, ex, live):
    set_settings(live, hold_timeout_enabled=False)
    rec = await svc.apply(msg(), BAN, [])
    assert rec.status is Status.PENDING
    assert rec.hold_applied is False
    assert ex.calls == [("delete", "m1")]


async def test_second_ban_request_merges(svc, ex, store):
    first = await svc.apply(msg("first text", mid="m1"), BAN, ["ctx"])
    second = await svc.apply(msg("second text", mid="m2"), BAN, [])
    assert second.id == first.id
    assert len(await store.list_actions()) == 1
    assert [c[0] for c in ex.calls].count("timeout") == 1
    assert ex.calls.count(("delete", "m1")) == 1
    assert ex.calls.count(("delete", "m2")) == 1
    assert second.context == ["ctx", "second text"]
    assert first.message_text == "first text"


async def test_concurrent_approve_first_wins(svc, ex):
    rec = await svc.apply(msg(), BAN, [])
    results = await asyncio.gather(
        svc.approve(rec.id, "streamer"),
        svc.approve(rec.id, "Brakkenhoff"),
        return_exceptions=True,
    )
    assert sum(isinstance(r, AlreadyResolved) for r in results) == 1
    assert [c for c in ex.calls if c[0] == "ban"] == [("ban", "u1", "Motwi (streamer): slurs")]
    done = next(r for r in results if not isinstance(r, Exception))
    assert done.status is Status.DONE
    assert done.actor == "streamer"


async def test_approve_pending_ban_records_offense(svc, offenses):
    rec = await svc.apply(msg(), BAN, [])
    assert offenses.last_index("u1", time.time()) is None
    await svc.approve(rec.id, "streamer")
    assert offenses.last_index("u1", time.time()) == 3


async def test_deny_lifts_hold(svc, ex):
    rec = await svc.apply(msg(), BAN, [])
    denied = await svc.deny(rec.id, "streamer")
    assert denied.status is Status.DENIED
    assert denied.actor == "streamer"
    assert ex.calls[-1] == ("unban", "u1")
    with pytest.raises(AlreadyResolved):
        await svc.deny(rec.id, "streamer")


async def test_deny_without_hold_does_not_unban(svc, ex, live):
    set_settings(live, hold_timeout_enabled=False)
    rec = await svc.apply(msg(), BAN, [])
    await svc.deny(rec.id, "streamer")
    assert ("unban", "u1") not in ex.calls


async def test_undo_timeout_unbans_and_clears_offense(svc, ex, offenses):
    rec = await svc.apply(msg(), TIMEOUT, [])
    undone = await svc.undo(rec.id, "streamer")
    assert undone.status is Status.UNDONE
    assert undone.actor == "streamer"
    assert ex.calls[-1] == ("unban", "u1")
    assert offenses.last_index("u1", time.time()) is None
    with pytest.raises(AlreadyResolved):
        await svc.undo(rec.id, "streamer")


async def test_undo_delete_marks_only(svc, ex, offenses):
    rec = await svc.apply(msg(), outcome(Kind.DELETE, ladder_index=-1), [])
    calls_before = list(ex.calls)
    undone = await svc.undo(rec.id, "streamer")
    assert undone.status is Status.UNDONE
    assert ex.calls == calls_before
    assert offenses.last_index("u1", time.time()) is None


async def test_undo_approved_ban_unbans(svc, ex):
    rec = await svc.apply(msg(), BAN, [])
    await svc.approve(rec.id, "streamer")
    undone = await svc.undo(rec.id, "streamer")
    assert undone.status is Status.UNDONE
    assert ex.calls[-1] == ("unban", "u1")


async def test_undo_requires_done(svc):
    rec = await svc.apply(msg(), BAN, [])
    with pytest.raises(AlreadyResolved):
        await svc.undo(rec.id, "streamer")


async def test_expire_overdue_never_bans(store, ex, bus, live, offenses):
    now = time.time()
    svc = ModerationService(
        store=store, executor=ex, bus=bus, live=live, offenses=offenses, clock=lambda: now
    )
    rec = await svc.apply(msg(), BAN, [])
    assert await svc.expire_overdue() == []

    now += 601
    q = bus.subscribe()
    expired = await svc.expire_overdue()
    assert [a.id for a in expired] == [rec.id]
    assert expired[0].status is Status.EXPIRED
    assert not any(c[0] == "ban" for c in ex.calls)
    kinds = [type(q.get_nowait()) for _ in range(q.qsize())]
    assert kinds == [ActionChanged, BanRequestReminder]
    assert await svc.expire_overdue() == []


async def test_events_published_for_each_change(svc, bus):
    q = bus.subscribe()
    rec = await svc.apply(msg(), BAN, [])
    await svc.apply(msg("again", mid="m2"), BAN, [])
    await svc.approve(rec.id, "streamer")
    await svc.undo(rec.id, "streamer")
    events = [q.get_nowait() for _ in range(q.qsize())]
    assert all(isinstance(e, ActionChanged) for e in events)
    assert [e.action.status for e in events] == [
        Status.PENDING,
        Status.PENDING,  # merge
        Status.DONE,
        Status.UNDONE,
    ]


async def test_suggest_only_second_ban_request_merges(svc, ex, live):
    set_settings(live, suggest_only=True)
    first = await svc.apply(msg("first text", mid="m1"), BAN, ["ctx"])
    second = await svc.apply(msg("second text", mid="m2"), BAN, [])
    assert second.id == first.id and second.status is Status.SUGGESTED
    assert second.context == ["ctx", "second text"]
    assert ex.calls == []
