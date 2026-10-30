import logging
from dataclasses import replace

import pytest
from conftest import make_new_action

from motwi.models import Ceiling, Kind, Source, Status
from motwi.settings import Settings
from motwi.store import Store, Usage


async def test_invalid_settings_rejected_previous_kept(store):
    await store.save_settings(Settings(strictness="strict"))
    bad = Settings(strictness="custom", custom_thresholds=None)
    with pytest.raises(ValueError):
        await store.save_settings(bad)
    assert (await store.get_settings()).strictness == "strict"


async def test_defaults_when_empty(store):
    assert await store.get_settings() == Settings()


async def test_corrupt_settings_returns_defaults_without_overwriting(store, caplog):
    await store._run(lambda c: c.execute("INSERT INTO settings(id, json) VALUES (1, 'not json{')"))
    with caplog.at_level(logging.WARNING):
        assert await store.get_settings() == Settings()
    assert "settings" in caplog.text.lower()
    row = await store._run(lambda c: c.execute("SELECT json FROM settings").fetchone())
    assert row[0] == "not json{"


async def test_rules_and_stream_clear(store):
    await store.add_rule("No spoilers", Ceiling.DELETE, "stream")
    await store.add_rule("No hate", Ceiling.BAN, "community")
    assert await store.clear_stream_rules() == 1
    assert [r.text for r in await store.list_rules()] == ["No hate"]


async def test_rule_update_and_delete(store):
    r = await store.add_rule("a", Ceiling.WARN, "community")
    assert len(r.id) == 8
    await store.update_rule(replace(r, text="b", ceiling=Ceiling.BAN))
    got = (await store.list_rules())[0]
    assert (got.text, got.ceiling) == ("b", Ceiling.BAN)
    await store.delete_rule(r.id)
    assert await store.list_rules() == []


async def test_blocked_words(store):
    w = await store.add_blocked("badword", base_index=2, ceiling=Ceiling.BAN)
    d = await store.add_blocked("other")
    assert d.base_index == -1 and d.ceiling is Ceiling.TIMEOUT
    assert {x.id for x in await store.list_blocked()} == {w.id, d.id}
    await store.delete_blocked(w.id)
    assert [x.id for x in await store.list_blocked()] == [d.id]


async def test_trusted_and_vip_exclusive(store):
    await store.add_person("vip", "Kofi_Draws")
    await store.add_person("trusted", "kofi_draws")
    assert await store.list_people("vip") == []
    assert await store.list_people("trusted") == ["kofi_draws"]


async def test_remove_person_only_matching_kind(store):
    await store.add_person("vip", "A")
    await store.remove_person("trusted", "a")
    assert await store.list_people("vip") == ["a"]
    await store.remove_person("vip", "A")
    assert await store.list_people("vip") == []


async def test_action_lifecycle_and_pending_lookup(store):
    a = await store.insert_action(make_new_action(kind=Kind.BAN_REQUEST, status=Status.PENDING))
    assert a.created_at > 0 and a.context == ["earlier line"] and a.source is Source.AI
    assert (await store.find_pending_ban(a.user_id)).id == a.id
    b = await store.update_action(a.id, status=Status.DONE, actor="streamer")
    assert b.status is Status.DONE and b.updated_at >= a.updated_at
    assert b.actor == "streamer" and b.created_at == a.created_at
    assert await store.find_pending_ban(a.user_id) is None
    assert await store.get_action(a.id) == b
    assert await store.get_action(9999) is None


async def test_update_action_bumps_updated_at(store):
    a = await store.insert_action(make_new_action())
    await store._run(lambda c: c.execute("UPDATE actions SET updated_at=1 WHERE id=?", (a.id,)))
    b = await store.update_action(a.id, hold_applied=True, error="x")
    assert b.updated_at > 1 and b.created_at == a.created_at
    assert b.hold_applied is True and b.error == "x"


async def test_list_actions_filters_and_order(store):
    a1 = await store.insert_action(make_new_action(user_login="a", status=Status.DONE))
    a2 = await store.insert_action(make_new_action(user_login="b", status=Status.PENDING))
    a3 = await store.insert_action(make_new_action(user_login="a", status=Status.PENDING))
    assert [x.id for x in await store.list_actions()] == [a3.id, a2.id, a1.id]
    assert [x.id for x in await store.list_actions(limit=1)] == [a3.id]
    assert [x.id for x in await store.list_actions(before_id=a3.id)] == [a2.id, a1.id]
    assert [x.id for x in await store.list_actions(user_login="a")] == [a3.id, a1.id]
    assert [x.id for x in await store.list_actions(status=Status.PENDING)] == [a3.id, a2.id]


async def test_usage_totals_and_purge(store):
    await store.record_usage(Usage(100.0, "gemini", "m", 10, 5, 0.01, 3, "s1"))
    await store.record_usage(Usage(200.0, "gemini", "m", 20, 6, None, 4, "s1"))
    t = await store.usage_totals()
    assert (t.requests, t.messages, t.prompt_tokens, t.completion_tokens) == (2, 7, 30, 11)
    assert t.cost_usd == pytest.approx(0.01)
    since = await store.usage_totals(since=150.0)
    assert since.requests == 1 and since.cost_usd == 0.0

    old = await store.insert_action(make_new_action())
    await store._run(lambda c: c.execute("UPDATE actions SET created_at=100 WHERE id=?", (old.id,)))
    new = await store.insert_action(make_new_action(message_id="m2"))
    await store.record_unchecked("x1", "u", "timeout")
    await store._run(lambda c: c.execute("UPDATE unchecked SET ts=100"))
    await store.record_unchecked("x2", "u", "timeout")
    assert await store.count_unchecked() == 2
    assert await store.count_unchecked(since=150.0) == 1

    removed = await store.purge(older_than_ts=150.0)
    assert removed == 2
    assert [a.id for a in await store.list_actions()] == [new.id]
    assert await store.count_unchecked() == 1
    assert (await store.usage_totals()).requests == 2


async def test_persists_across_reopen(tmp_path):
    path = tmp_path / "m.db"
    s = await Store.open(path)
    await s.save_settings(Settings(strictness="strict"))
    await s.close()
    s2 = await Store.open(path)
    assert (await s2.get_settings()).strictness == "strict"
    await s2.close()


async def test_v1_database_upgrades_and_keeps_level(tmp_path):
    import sqlite3

    from motwi.store import MIGRATIONS

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(f"BEGIN;\n{MIGRATIONS[0]}\nPRAGMA user_version=1;\nCOMMIT;")
    conn.close()
    store = await Store.open(path)
    try:
        rec = await store.insert_action(make_new_action(level=3))
        assert (await store.get_action(rec.id)).level == 3
    finally:
        await store.close()


async def test_history_filters_combine(store):
    from motwi.store import ActionFilter

    await store.insert_action(make_new_action(message_text="100% legit", user_login="a"))
    await store.insert_action(make_new_action(message_text="hello", actor="mod1"))
    await store.insert_action(make_new_action(kind=Kind.TIMEOUT, user_login="b"))
    by_text = await store.list_actions(where=ActionFilter(query="100%"))
    assert [a.message_text for a in by_text] == ["100% legit"]
    assert await store.list_actions(where=ActionFilter(query="_")) == []
    by_actor = await store.list_actions(where=ActionFilter(actor="motwi"))
    assert len(by_actor) == 2
    mods = await store.list_actions(where=ActionFilter(actor="mod1"))
    assert [a.actor for a in mods] == ["mod1"]
    kinds = await store.list_actions(where=ActionFilter(kinds=(Kind.TIMEOUT,), user_login="B"))
    assert [a.user_login for a in kinds] == ["b"]


async def test_streams_and_checked_counts(store):
    await store.stream_started("s1", 100.0, "t", "g")
    await store.stream_started("s1", 100.0, "t2", "g")  # a reconnect while live
    await store.stream_ended("s1", 200.0)
    [s] = await store.list_streams()
    assert (s.title, s.ended_at) == ("t2", 200.0)
    await store.add_checked({("2026-10-27", "s1"): 3, ("2026-10-28", None): 2})
    await store.add_checked({("2026-10-27", "s1"): 1})
    assert await store.count_checked() == 6
    assert await store.count_checked(stream_id="s1") == 4
    assert await store.count_checked(since_day="2026-10-28") == 2


async def test_hidden_blocked_word_round_trips(store):
    await store.add_blocked("slur", hidden=True)
    [w] = await store.list_blocked()
    assert w.hidden is True
