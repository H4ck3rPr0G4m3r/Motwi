"""SQLite persistence. One connection, owned by one worker thread."""

import asyncio
import json
import logging
import sqlite3
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, fields, replace
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
    # escalation level earned by the offense, separate from the executed ladder step
    """
    ALTER TABLE actions ADD COLUMN level INTEGER;
    """,
    # the app: hidden blocked words, stream boundaries, checked-message counts
    """
    ALTER TABLE blocked_words ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0;
    CREATE TABLE streams (
        id TEXT PRIMARY KEY,
        started_at REAL NOT NULL,
        ended_at REAL,
        title TEXT,
        game TEXT
    );
    CREATE TABLE message_counts (
        day TEXT NOT NULL,
        stream_id TEXT NOT NULL DEFAULT '',
        checked INTEGER NOT NULL,
        PRIMARY KEY (day, stream_id)
    );
    CREATE INDEX idx_actions_stream ON actions(stream_id);
    CREATE INDEX idx_usage_ts ON ai_usage(ts);
    """,
    # browsers paired with this Motwi; only token hashes are kept
    """
    CREATE TABLE clients (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        label TEXT NOT NULL,
        token_hash TEXT NOT NULL UNIQUE,
        created_at REAL NOT NULL,
        last_seen REAL
    );
    """,
]

# what a stats total counts: everything except suggestions nobody took
_COUNTED = ("pending", "done", "failed", "undone", "denied", "expired")


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
    level: int | None = None


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
    level: int | None = None


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


@dataclass(frozen=True)
class StreamRecord:
    id: str
    started_at: float
    ended_at: float | None
    title: str | None
    game: str | None


@dataclass(frozen=True)
class ActionFilter:
    """History filters. Every field narrows the result; None means any."""

    kinds: tuple[Kind, ...] | None = None
    statuses: tuple[Status, ...] | None = None
    rule_id: str | None = None
    source: Source | None = None
    user_login: str | None = None
    stream_id: str | None = None
    actor: str | None = None  # "motwi" matches actions nobody took by hand
    query: str | None = None
    since: float | None = None


@dataclass(frozen=True)
class ClientRecord:
    id: str
    kind: str
    label: str
    token_hash: str
    created_at: float
    last_seen: float | None


@dataclass(frozen=True)
class Chatter:
    user_id: str
    user_login: str
    actions: int
    last_at: float
    last_kind: Kind


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


def _filter_sql(f: ActionFilter) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if f.kinds is not None:
        clauses.append(f"kind IN ({', '.join('?' * len(f.kinds))})")
        params += [k.value for k in f.kinds]
    if f.statuses is not None:
        clauses.append(f"status IN ({', '.join('?' * len(f.statuses))})")
        params += [s.value for s in f.statuses]
    for column, value in (
        ("rule_id", f.rule_id),
        ("source", f.source.value if f.source else None),
        ("user_login", f.user_login.lower() if f.user_login else None),
        ("stream_id", f.stream_id),
    ):
        if value is not None:
            clauses.append(f"{column} = ?")
            params.append(value)
    if f.actor == "motwi":
        clauses.append("actor IS NULL")
    elif f.actor is not None:
        clauses.append("actor = ?")
        params.append(f.actor)
    if f.query:
        like = "%" + f.query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        clauses.append(
            "(message_text LIKE ? ESCAPE '\\' OR user_login LIKE ? ESCAPE '\\' "
            "OR reason LIKE ? ESCAPE '\\')"
        )
        params += [like, like, like]
    if f.since is not None:
        clauses.append("created_at >= ?")
        params.append(f.since)
    return clauses, params


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

    # paired clients

    async def add_client(self, kind: str, label: str, token_hash: str) -> ClientRecord:
        record = ClientRecord(uuid4().hex, kind, label, token_hash, time.time(), None)
        await self._write(
            lambda c: c.execute(
                "INSERT INTO clients(id, kind, label, token_hash, created_at, last_seen) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    record.id,
                    record.kind,
                    record.label,
                    record.token_hash,
                    record.created_at,
                    record.last_seen,
                ),
            )
        )
        return record

    async def list_clients(self) -> list[ClientRecord]:
        rows = await self._run(
            lambda c: c.execute(
                "SELECT id, kind, label, token_hash, created_at, last_seen FROM clients "
                "ORDER BY created_at"
            ).fetchall()
        )
        return [ClientRecord(*row) for row in rows]

    async def delete_client(self, client_id: str) -> bool:
        cur = await self._write(lambda c: c.execute("DELETE FROM clients WHERE id=?", (client_id,)))
        return cur.rowcount > 0

    async def touch_client(self, client_id: str, ts: float) -> None:
        await self._write(
            lambda c: c.execute("UPDATE clients SET last_seen=? WHERE id=?", (ts, client_id))
        )

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
                "SELECT id, phrase, base_index, ceiling, hidden FROM blocked_words ORDER BY seq"
            ).fetchall()
        )
        return [BlockedWord(r[0], r[1], r[2], Ceiling(r[3]), bool(r[4])) for r in rows]

    async def add_blocked(
        self,
        phrase: str,
        base_index: int = -1,
        ceiling: Ceiling = Ceiling.TIMEOUT,
        hidden: bool = False,
    ) -> BlockedWord:
        word = BlockedWord(uuid4().hex[:8], phrase, base_index, ceiling, hidden)
        await self._write(
            lambda c: c.execute(
                "INSERT INTO blocked_words(id, phrase, base_index, ceiling, hidden, seq) "
                "VALUES (?, ?, ?, ?, ?, (SELECT COALESCE(MAX(seq), 0) + 1 FROM blocked_words))",
                (word.id, phrase, base_index, ceiling.value, int(hidden)),
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
        where: ActionFilter | None = None,
    ) -> list[ActionRecord]:
        f = where or ActionFilter()
        if user_login is not None:
            f = replace(f, user_login=user_login)
        if status is not None:
            f = replace(f, statuses=(status,))
        clauses, params = _filter_sql(f)
        if before_id is not None:
            clauses.append("id < ?")
            params.append(before_id)
        clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT {_SELECT_ACTION} FROM actions {clause} ORDER BY id DESC LIMIT ?"
        rows = await self._run(lambda c: c.execute(sql, [*params, limit]).fetchall())
        return [_action_from_row(r) for r in rows]

    async def recent_chatters(self, limit: int = 20) -> list[Chatter]:
        """Chatters Motwi acted on, most recent first, with their action count."""
        sql = (
            "SELECT user_id, user_login, COUNT(*), MAX(created_at), "
            "(SELECT kind FROM actions b WHERE b.user_id = a.user_id ORDER BY id DESC LIMIT 1) "
            f"FROM actions a WHERE status IN ({', '.join('?' * len(_COUNTED))}) "
            "GROUP BY user_id ORDER BY MAX(created_at) DESC LIMIT ?"
        )
        rows = await self._run(lambda c: c.execute(sql, [*_COUNTED, limit]).fetchall())
        return [Chatter(r[0], r[1], r[2], r[3], Kind(r[4])) for r in rows]

    # stats

    async def action_counts(
        self, *, since: float | None = None, stream_id: str | None = None
    ) -> dict[tuple[Kind, Status], int]:
        clauses, params = _filter_sql(ActionFilter(since=since, stream_id=stream_id))
        clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = await self._run(
            lambda c: c.execute(
                f"SELECT kind, status, COUNT(*) FROM actions {clause} GROUP BY kind, status",
                params,
            ).fetchall()
        )
        return {(Kind(r[0]), Status(r[1])): r[2] for r in rows}

    async def actions_per_day(
        self, *, since: float | None = None, stream_id: str | None = None
    ) -> list[tuple[str, Kind, int]]:
        """(local day, kind, count) for counted actions, oldest day first."""
        clauses, params = _filter_sql(
            ActionFilter(since=since, stream_id=stream_id, statuses=tuple(map(Status, _COUNTED)))
        )
        sql = (
            "SELECT date(created_at, 'unixepoch', 'localtime') AS day, kind, COUNT(*) "
            f"FROM actions WHERE {' AND '.join(clauses)} GROUP BY day, kind ORDER BY day"
        )
        rows = await self._run(lambda c: c.execute(sql, params).fetchall())
        return [(r[0], Kind(r[1]), r[2]) for r in rows]

    async def triggers(
        self, *, since: float | None = None, stream_id: str | None = None
    ) -> list[tuple[Source, str | None, int]]:
        """(source, rule id, count) for counted actions, most frequent first."""
        clauses, params = _filter_sql(
            ActionFilter(since=since, stream_id=stream_id, statuses=tuple(map(Status, _COUNTED)))
        )
        sql = (
            "SELECT source, rule_id, COUNT(*) AS n FROM actions "
            f"WHERE {' AND '.join(clauses)} GROUP BY source, rule_id ORDER BY n DESC"
        )
        rows = await self._run(lambda c: c.execute(sql, params).fetchall())
        return [(Source(r[0]), r[1], r[2]) for r in rows]

    async def find_pending_ban(
        self, user_id: str, statuses: tuple[Status, ...] = (Status.PENDING,)
    ) -> ActionRecord | None:
        marks = ", ".join("?" * len(statuses))
        row = await self._run(
            lambda c: c.execute(
                f"SELECT {_SELECT_ACTION} FROM actions "
                f"WHERE user_id=? AND kind=? AND status IN ({marks}) ORDER BY id DESC LIMIT 1",
                (user_id, Kind.BAN_REQUEST.value, *(s.value for s in statuses)),
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

    async def usage_totals(
        self, since: float | None = None, stream_id: str | None = None
    ) -> UsageTotals:
        clauses: list[str] = []
        params: list[Any] = []
        if since is not None:
            clauses.append("ts >= ?")
            params.append(since)
        if stream_id is not None:
            clauses.append("stream_id = ?")
            params.append(stream_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        row = await self._run(
            lambda c: c.execute(
                "SELECT COUNT(*), COALESCE(SUM(messages), 0), COALESCE(SUM(prompt_tokens), 0), "
                f"COALESCE(SUM(completion_tokens), 0), COALESCE(SUM(cost_usd), 0.0) "
                f"FROM ai_usage {where}",
                params,
            ).fetchone()
        )
        return UsageTotals(row[0], row[1], row[2], row[3], float(row[4]))

    # streams

    async def stream_started(
        self, stream_id: str, started_at: float, title: str | None, game: str | None
    ) -> None:
        await self._write(
            lambda c: c.execute(
                "INSERT INTO streams(id, started_at, title, game) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET title=excluded.title, game=excluded.game",
                (stream_id, started_at, title, game),
            )
        )

    async def stream_ended(self, stream_id: str, ended_at: float) -> None:
        await self._write(
            lambda c: c.execute(
                "UPDATE streams SET ended_at=? WHERE id=? AND ended_at IS NULL",
                (ended_at, stream_id),
            )
        )

    async def list_streams(
        self, *, since: float | None = None, limit: int = 100
    ) -> list[StreamRecord]:
        where, params = ("WHERE started_at >= ?", [since]) if since is not None else ("", [])
        rows = await self._run(
            lambda c: c.execute(
                "SELECT id, started_at, ended_at, title, game FROM streams "
                f"{where} ORDER BY started_at DESC LIMIT ?",
                [*params, limit],
            ).fetchall()
        )
        return [StreamRecord(*r) for r in rows]

    # checked-message counts

    async def add_checked(self, counts: dict[tuple[str, str | None], int]) -> None:
        """Adds to the checked counts, keyed by (local day YYYY-MM-DD, stream id)."""
        rows = [(day, stream or "", n) for (day, stream), n in counts.items() if n]
        if not rows:
            return
        await self._write(
            lambda c: c.executemany(
                "INSERT INTO message_counts(day, stream_id, checked) VALUES (?, ?, ?) "
                "ON CONFLICT(day, stream_id) DO UPDATE SET checked = checked + excluded.checked",
                rows,
            )
        )

    async def count_checked(
        self, *, since_day: str | None = None, stream_id: str | None = None
    ) -> int:
        clauses: list[str] = []
        params: list[Any] = []
        if since_day is not None:
            clauses.append("day >= ?")
            params.append(since_day)
        if stream_id is not None:
            clauses.append("stream_id = ?")
            params.append(stream_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        row = await self._run(
            lambda c: c.execute(
                f"SELECT COALESCE(SUM(checked), 0) FROM message_counts {where}", params
            ).fetchone()
        )
        return row[0]

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
            day = time.strftime("%Y-%m-%d", time.localtime(older_than_ts))
            n = c.execute("DELETE FROM message_counts WHERE day < ?", (day,)).rowcount
            return a + u + n

        return await self._write(work)
