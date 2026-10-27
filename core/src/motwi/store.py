"""SQLite persistence. One connection, owned by one worker thread."""

import asyncio
import json
import logging
import sqlite3
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Literal, Self, TypeVar
from uuid import uuid4

from motwi.filters.blocked import BlockedWord
from motwi.models import Ceiling, Kind, Rule, Source, Status
from motwi.settings import Settings

log = logging.getLogger(__name__)

T = TypeVar("T")

MIGRATIONS: list[str] = [
    """
    CREATE TABLE settings (
        id INTEGER PRIMARY KEY CHECK(id = 1),
        json TEXT NOT NULL
    );
    CREATE TABLE rules (
        id TEXT PRIMARY KEY,
        text TEXT NOT NULL,
        ceiling TEXT NOT NULL,
        scope TEXT NOT NULL CHECK(scope IN ('community', 'stream')),
        seq INTEGER NOT NULL
    );
    CREATE TABLE blocked_words (
        id TEXT PRIMARY KEY,
        phrase TEXT NOT NULL,
        base_index INTEGER NOT NULL DEFAULT -1,
        ceiling TEXT NOT NULL,
        seq INTEGER NOT NULL
    );
    CREATE TABLE people (
        login TEXT PRIMARY KEY,
        kind TEXT NOT NULL CHECK(kind IN ('trusted', 'vip'))
    );
    CREATE TABLE actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        stream_id TEXT,
        user_id TEXT NOT NULL,
        user_login TEXT NOT NULL,
        message_id TEXT NOT NULL,
        message_text TEXT NOT NULL,
        context TEXT NOT NULL,
        source TEXT NOT NULL,
        rule_id TEXT,
        score REAL,
        reason TEXT NOT NULL,
        kind TEXT NOT NULL,
        "delete" INTEGER NOT NULL,
        duration_s INTEGER,
        ladder_index INTEGER,
        status TEXT NOT NULL,
        actor TEXT,
        hold_applied INTEGER NOT NULL DEFAULT 0,
        error TEXT
    );
    CREATE INDEX idx_actions_user_status ON actions(user_id, status);
    CREATE INDEX idx_actions_created ON actions(created_at);
    CREATE TABLE ai_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        prompt_tokens INTEGER NOT NULL,
        completion_tokens INTEGER NOT NULL,
        cost_usd REAL,
        messages INTEGER NOT NULL,
        stream_id TEXT
    );
    CREATE TABLE unchecked (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        message_id TEXT NOT NULL,
        user_login TEXT NOT NULL,
        reason TEXT NOT NULL
    );
    CREATE INDEX idx_unchecked_ts ON unchecked(ts);
    """,
]


@dataclass(frozen=True)
class NewAction:
    stream_id: str | None
    user_id: str
    user_login: str
    message_id: str
    message_text: str
    context: list[str]
    source: Source
    rule_id: str | None
    score: float | None
    reason: str
    kind: Kind
    delete: bool
    duration_s: int | None
    ladder_index: int | None
    status: Status
    actor: str | None
    hold_applied: bool
    error: str | None


@dataclass(frozen=True)
class ActionRecord:
    id: int
    created_at: float
    updated_at: float
    stream_id: str | None
    user_id: str
    user_login: str
    message_id: str
    message_text: str
    context: list[str]
    source: Source
    rule_id: str | None
    score: float | None
    reason: str
    kind: Kind
    delete: bool
    duration_s: int | None
    ladder_index: int | None
    status: Status
    actor: str | None
    hold_applied: bool
    error: str | None


@dataclass(frozen=True)
class Usage:
    ts: float
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float | None
    messages: int
    stream_id: str | None


@dataclass(frozen=True)
class UsageTotals:
    requests: int
    messages: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float


_NEW_ACTION_COLS = [f.name for f in fields(NewAction)]
_ACTION_COLS = [f.name for f in fields(ActionRecord)]
_SELECT_ACTION = ", ".join(f'"{c}"' for c in _ACTION_COLS)


def _encode_action_value(name: str, value: Any) -> Any:
    if name == "context":
        return json.dumps(value)
    if name in ("delete", "hold_applied"):
        return int(value)
    if isinstance(value, (Source, Kind, Status)):
        return value.value
    return value


def _action_from_row(row: tuple) -> ActionRecord:
    d = dict(zip(_ACTION_COLS, row, strict=True))
    d["context"] = json.loads(d["context"])
    d["source"] = Source(d["source"])
    d["kind"] = Kind(d["kind"])
    d["status"] = Status(d["status"])
    d["delete"] = bool(d["delete"])
    d["hold_applied"] = bool(d["hold_applied"])
    return ActionRecord(**d)


class Store:
    def __init__(self, conn: sqlite3.Connection, executor: ThreadPoolExecutor) -> None:
        self._conn = conn
        self._ex = executor

    @classmethod
    async def open(cls, path: Path | str) -> Self:
        ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix="motwi-db")

        def connect() -> sqlite3.Connection:
            conn = sqlite3.connect(str(path), check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            for i, script in enumerate(MIGRATIONS[version:], start=version + 1):
                conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version={i};\nCOMMIT;")
            return conn

        try:
            conn = await asyncio.get_running_loop().run_in_executor(ex, connect)
        except BaseException:
            ex.shutdown(wait=False)
            raise
        return cls(conn, ex)

    async def _run(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        return await asyncio.get_running_loop().run_in_executor(self._ex, fn, self._conn)

    async def _write(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """Run fn and commit atomically (rolling back on error)."""

        def work(conn: sqlite3.Connection) -> T:
            try:
                result = fn(conn)
                conn.commit()
                return result
            except BaseException:
                conn.rollback()
                raise

        return await self._run(work)

    async def close(self) -> None:
        await self._run(lambda c: c.close())
        self._ex.shutdown(wait=True)

    # settings

    async def get_settings(self) -> Settings:
        row = await self._run(
            lambda c: c.execute("SELECT json FROM settings WHERE id=1").fetchone()
        )
        if row is None:
            return Settings()
        try:
            return Settings.from_dict(json.loads(row[0]))
        except (ValueError, TypeError, KeyError, AttributeError) as e:
            log.warning(
                "stored settings are unreadable, using defaults (row left untouched): %s", e
            )
            return Settings()

    async def save_settings(self, s: Settings) -> None:
        s.validate()
        payload = json.dumps(s.to_dict())
        await self._write(
            lambda c: c.execute(
                "INSERT INTO settings(id, json) VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET json=excluded.json",
                (payload,),
            )
        )

    # rules

    async def list_rules(self) -> list[Rule]:
        rows = await self._run(
            lambda c: c.execute(
                "SELECT id, text, ceiling, scope FROM rules ORDER BY seq"
            ).fetchall()
        )
        return [Rule(r[0], r[1], Ceiling(r[2]), r[3]) for r in rows]

    async def add_rule(
        self, text: str, ceiling: Ceiling, scope: Literal["community", "stream"]
    ) -> Rule:
        rule = Rule(uuid4().hex[:8], text, ceiling, scope)
        await self._write(
            lambda c: c.execute(
                "INSERT INTO rules(id, text, ceiling, scope, seq) "
                "VALUES (?, ?, ?, ?, (SELECT COALESCE(MAX(seq), 0) + 1 FROM rules))",
                (rule.id, text, ceiling.value, scope),
            )
        )
        return rule

    async def update_rule(self, rule: Rule) -> None:
        await self._write(
            lambda c: c.execute(
                "UPDATE rules SET text=?, ceiling=?, scope=? WHERE id=?",
                (rule.text, rule.ceiling.value, rule.scope, rule.id),
            )
        )

    async def delete_rule(self, rule_id: str) -> None:
        await self._write(lambda c: c.execute("DELETE FROM rules WHERE id=?", (rule_id,)))

    async def clear_stream_rules(self) -> int:
        return await self._write(
            lambda c: c.execute("DELETE FROM rules WHERE scope='stream'").rowcount
        )

    # blocked words

    async def list_blocked(self) -> list[BlockedWord]:
        rows = await self._run(
            lambda c: c.execute(
                "SELECT id, phrase, base_index, ceiling FROM blocked_words ORDER BY seq"
            ).fetchall()
        )
        return [BlockedWord(r[0], r[1], r[2], Ceiling(r[3])) for r in rows]

    async def add_blocked(
        self, phrase: str, base_index: int = -1, ceiling: Ceiling = Ceiling.TIMEOUT
    ) -> BlockedWord:
        word = BlockedWord(uuid4().hex[:8], phrase, base_index, ceiling)
        await self._write(
            lambda c: c.execute(
                "INSERT INTO blocked_words(id, phrase, base_index, ceiling, seq) "
                "VALUES (?, ?, ?, ?, (SELECT COALESCE(MAX(seq), 0) + 1 FROM blocked_words))",
                (word.id, phrase, base_index, ceiling.value),
            )
        )
        return word

    async def delete_blocked(self, word_id: str) -> None:
        await self._write(lambda c: c.execute("DELETE FROM blocked_words WHERE id=?", (word_id,)))

    # people

    async def list_people(self, kind: Literal["trusted", "vip"]) -> list[str]:
        rows = await self._run(
            lambda c: c.execute(
                "SELECT login FROM people WHERE kind=? ORDER BY login", (kind,)
            ).fetchall()
        )
        return [r[0] for r in rows]

    async def add_person(self, kind: Literal["trusted", "vip"], login: str) -> None:
        """Adds login as kind; a login is only ever one kind, so this replaces the other."""
        await self._write(
            lambda c: c.execute(
                "INSERT OR REPLACE INTO people(login, kind) VALUES (?, ?)", (login.lower(), kind)
            )
        )

    async def remove_person(self, kind: Literal["trusted", "vip"], login: str) -> None:
        await self._write(
            lambda c: c.execute(
                "DELETE FROM people WHERE login=? AND kind=?", (login.lower(), kind)
            )
        )

    # actions

    async def insert_action(self, a: NewAction) -> ActionRecord:
        now = time.time()
        cols = ["created_at", "updated_at", *_NEW_ACTION_COLS]
        values = [now, now, *(_encode_action_value(n, getattr(a, n)) for n in _NEW_ACTION_COLS)]
        sql = (
            f"INSERT INTO actions({', '.join(f'"{c}"' for c in cols)}) "
            f"VALUES ({', '.join('?' * len(cols))})"
        )

        def work(c: sqlite3.Connection) -> ActionRecord:
            action_id = c.execute(sql, values).lastrowid
            return _action_from_row(
                c.execute(
                    f"SELECT {_SELECT_ACTION} FROM actions WHERE id=?", (action_id,)
                ).fetchone()
            )

        return await self._write(work)

    async def update_action(self, action_id: int, **changes: Any) -> ActionRecord:
        unknown = set(changes) - set(_NEW_ACTION_COLS)
        if unknown:
            raise ValueError(f"unknown action fields: {sorted(unknown)}")
        assignments = ["updated_at=?", *(f'"{n}"=?' for n in changes)]
        values = [time.time(), *(_encode_action_value(n, v) for n, v in changes.items())]

        def work(c: sqlite3.Connection) -> ActionRecord:
            cur = c.execute(
                f"UPDATE actions SET {', '.join(assignments)} WHERE id=?", [*values, action_id]
            )
            if cur.rowcount == 0:
                raise KeyError(f"no action with id {action_id}")
            return _action_from_row(
                c.execute(
                    f"SELECT {_SELECT_ACTION} FROM actions WHERE id=?", (action_id,)
                ).fetchone()
            )

        return await self._write(work)

    async def get_action(self, action_id: int) -> ActionRecord | None:
        row = await self._run(
            lambda c: c.execute(
                f"SELECT {_SELECT_ACTION} FROM actions WHERE id=?", (action_id,)
            ).fetchone()
        )
        return None if row is None else _action_from_row(row)

    async def list_actions(
        self,
        *,
        limit: int = 100,
        before_id: int | None = None,
        user_login: str | None = None,
        status: Status | None = None,
    ) -> list[ActionRecord]:
        where: list[str] = []
        params: list[Any] = []
        if before_id is not None:
            where.append("id < ?")
            params.append(before_id)
        if user_login is not None:
            where.append("user_login = ?")
            params.append(user_login)
        if status is not None:
            where.append("status = ?")
            params.append(status.value)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        sql = f"SELECT {_SELECT_ACTION} FROM actions {clause} ORDER BY id DESC LIMIT ?"
        rows = await self._run(lambda c: c.execute(sql, [*params, limit]).fetchall())
        return [_action_from_row(r) for r in rows]

    async def find_pending_ban(self, user_id: str) -> ActionRecord | None:
        row = await self._run(
            lambda c: c.execute(
                f"SELECT {_SELECT_ACTION} FROM actions "
                "WHERE user_id=? AND kind=? AND status=? ORDER BY id DESC LIMIT 1",
                (user_id, Kind.BAN_REQUEST.value, Status.PENDING.value),
            ).fetchone()
        )
        return None if row is None else _action_from_row(row)

    # usage

    async def record_usage(self, u: Usage) -> None:
        await self._write(
            lambda c: c.execute(
                "INSERT INTO ai_usage(ts, provider, model, prompt_tokens, completion_tokens, "
                "cost_usd, messages, stream_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    u.ts,
                    u.provider,
                    u.model,
                    u.prompt_tokens,
                    u.completion_tokens,
                    u.cost_usd,
                    u.messages,
                    u.stream_id,
                ),
            )
        )

    async def usage_totals(self, since: float | None = None) -> UsageTotals:
        where, params = ("WHERE ts >= ?", (since,)) if since is not None else ("", ())
        row = await self._run(
            lambda c: c.execute(
                "SELECT COUNT(*), COALESCE(SUM(messages), 0), COALESCE(SUM(prompt_tokens), 0), "
                f"COALESCE(SUM(completion_tokens), 0), COALESCE(SUM(cost_usd), 0.0) "
                f"FROM ai_usage {where}",
                params,
            ).fetchone()
        )
        return UsageTotals(row[0], row[1], row[2], row[3], float(row[4]))

    # unchecked

    async def record_unchecked(self, message_id: str, user_login: str, reason: str) -> None:
        now = time.time()
        await self._write(
            lambda c: c.execute(
                "INSERT INTO unchecked(ts, message_id, user_login, reason) VALUES (?, ?, ?, ?)",
                (now, message_id, user_login, reason),
            )
        )

    async def count_unchecked(self, since: float | None = None) -> int:
        where, params = ("WHERE ts >= ?", (since,)) if since is not None else ("", ())
        row = await self._run(
            lambda c: c.execute(f"SELECT COUNT(*) FROM unchecked {where}", params).fetchone()
        )
        return row[0]

    # retention

    async def purge(self, older_than_ts: float) -> int:
        def work(c: sqlite3.Connection) -> int:
            a = c.execute("DELETE FROM actions WHERE created_at < ?", (older_than_ts,)).rowcount
            u = c.execute("DELETE FROM unchecked WHERE ts < ?", (older_than_ts,)).rowcount
            return a + u

        return await self._write(work)
