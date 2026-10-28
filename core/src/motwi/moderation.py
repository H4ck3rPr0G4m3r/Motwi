"""Moderation service: turns decided plans into Twitch actions and tracks their lifecycle."""

import asyncio
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from motwi.engine.decide import Plan
from motwi.engine.offenses import OffenseTracker
from motwi.events import ActionChanged, BanRequestReminder, EventBus
from motwi.live import LiveConfig
from motwi.models import ChatMessage, Kind, Source, Status
from motwi.store import ActionRecord, NewAction, Store

_HOLD_REASON = "Motwi: held pending ban review"
_EXPIRE_BATCH = 1000


class ExecutorError(Exception):
    """A Twitch action failed."""


class Executor(Protocol):
    async def delete_message(self, message_id: str) -> None: ...
    async def warn(self, user_id: str, reason: str) -> None: ...
    async def send_chat(self, text: str) -> None: ...
    async def timeout(self, user_id: str, seconds: int, reason: str) -> None: ...
    async def ban(self, user_id: str, reason: str) -> None: ...
    async def unban(self, user_id: str) -> None: ...


class AlreadyResolved(Exception):
    """The action is no longer in a state that allows this operation."""


@dataclass(frozen=True)
class Outcome:
    source: Source
    plan: Plan
    score: float | None
    rule_id: str | None
    reason: str


def _twitch_reason(reason: str, actor: str | None) -> str:
    return f"Motwi: {reason}" if actor is None else f"Motwi ({actor}): {reason}"


class ModerationService:
    def __init__(
        self,
        *,
        store: Store,
        executor: Executor,
        bus: EventBus,
        live: LiveConfig,
        offenses: OffenseTracker,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._executor = executor
        self._bus = bus
        self._live = live
        self._offenses = offenses
        self._clock = clock
        self._locks: dict[int, asyncio.Lock] = {}

    # helpers

    def _lock(self, action_id: int) -> asyncio.Lock:
        return self._locks.setdefault(action_id, asyncio.Lock())

    def _publish(self, record: ActionRecord) -> ActionRecord:
        self._bus.publish(ActionChanged(record))
        return record

    async def _update(self, action_id: int, **changes: Any) -> ActionRecord:
        return self._publish(await self._store.update_action(action_id, **changes))

    def _record_offense(self, record: ActionRecord) -> None:
        level = record.level if record.level is not None else record.ladder_index
        if level is not None:
            self._offenses.record(record.user_id, level, record.id, self._clock())

    async def _load(self, action_id: int) -> ActionRecord:
        record = await self._store.get_action(action_id)
        if record is None:
            raise KeyError(f"no action with id {action_id}")
        return record

    async def _run_plan(
        self,
        *,
        message_id: str,
        user_id: str,
        user_login: str,
        kind: Kind,
        delete: bool,
        duration_s: int | None,
        reason: str,
    ) -> None:
        """Delete the message, then run the kind's action. Raises ExecutorError."""
        ex = self._executor
        if delete:
            await ex.delete_message(message_id)
        match kind:
            case Kind.WARN:
                if self._live.settings.warn_style == "chat":
                    await ex.send_chat(f"@{user_login} {reason}")
                else:
                    await ex.warn(user_id, reason)
            case Kind.TIMEOUT:
                if duration_s is None:
                    raise ExecutorError("timeout action has no duration")
                await ex.timeout(user_id, duration_s, reason)
            case Kind.BAN_REQUEST:
                # Only reached when a human approved a suggestion.
                await ex.ban(user_id, reason)
            case _:
                pass

    # public API

    async def apply(
        self, msg: ChatMessage, outcome: Outcome, context: Sequence[str]
    ) -> ActionRecord | None:
        plan = outcome.plan
        if plan.kind is Kind.NONE:
            return None

        def new(status: Status, **extra: Any) -> NewAction:
            fields: dict[str, Any] = {
                "stream_id": self._live.stream.stream_id,
                "user_id": msg.user_id,
                "user_login": msg.user_login,
                "message_id": msg.id,
                "message_text": msg.text,
                "context": list(context),
                "source": outcome.source,
                "rule_id": outcome.rule_id,
                "score": outcome.score,
                "reason": outcome.reason,
                "kind": plan.kind,
                "delete": plan.delete,
                "duration_s": plan.duration_s,
                "ladder_index": plan.ladder_index,
                "level": plan.level,
                "status": status,
                "actor": None,
                "hold_applied": False,
                "error": None,
            }
            fields.update(extra)
            return NewAction(**fields)

        if self._live.settings.suggest_only:
            if plan.kind is Kind.BAN_REQUEST:
                # one open ban request per chatter, suggested or not: never a second pop-up
                existing = await self._store.find_pending_ban(
                    msg.user_id, (Status.SUGGESTED, Status.PENDING)
                )
                if existing is not None:
                    return await self._update(existing.id, context=[*existing.context, msg.text])
            return self._publish(await self._store.insert_action(new(Status.SUGGESTED)))

        if plan.kind is Kind.BAN_REQUEST:
            return await self._apply_ban_request(msg, new)

        error: str | None = None
        try:
            await self._run_plan(
                message_id=msg.id,
                user_id=msg.user_id,
                user_login=msg.user_login,
                kind=plan.kind,
                delete=plan.delete,
                duration_s=plan.duration_s,
                reason=_twitch_reason(outcome.reason, None),
            )
        except ExecutorError as e:
            error = str(e)
        record = self._publish(
            await self._store.insert_action(
                new(Status.FAILED if error else Status.DONE, error=error)
            )
        )
        if error is None:
            self._record_offense(record)
        return record

    async def _apply_ban_request(
        self, msg: ChatMessage, new: Callable[..., NewAction]
    ) -> ActionRecord:
        settings = self._live.settings
        errors: list[str] = []
        try:
            await self._executor.delete_message(msg.id)
        except ExecutorError as e:
            errors.append(str(e))

        existing = await self._store.find_pending_ban(msg.user_id)
        if existing is not None:
            changes: dict[str, Any] = {"context": [*existing.context, msg.text]}
            if errors:
                changes["error"] = "; ".join(errors)
            return await self._update(existing.id, **changes)

        hold = False
        if settings.hold_timeout_enabled:
            try:
                await self._executor.timeout(msg.user_id, settings.hold_timeout_s, _HOLD_REASON)
                hold = True
            except ExecutorError as e:
                errors.append(str(e))
        record = await self._store.insert_action(
            new(
                Status.PENDING,
                hold_applied=hold,
                error="; ".join(errors) if errors else None,
            )
        )
        return self._publish(record)

    async def approve(self, action_id: int, actor: str) -> ActionRecord:
        async with self._lock(action_id):
            record = await self._load(action_id)
            if record.status not in (Status.PENDING, Status.SUGGESTED):
                raise AlreadyResolved(f"action {action_id} is {record.status.value}")
            # A pending ban request already had its message deleted when it was raised.
            already_deleted = record.status is Status.PENDING
            error: str | None = None
            try:
                await self._run_plan(
                    message_id=record.message_id,
                    user_id=record.user_id,
                    user_login=record.user_login,
                    kind=record.kind,
                    delete=record.delete and not already_deleted,
                    duration_s=record.duration_s,
                    reason=_twitch_reason(record.reason, actor),
                )
            except ExecutorError as e:
                error = str(e)
            updated = await self._update(
                action_id,
                status=Status.FAILED if error else Status.DONE,
                actor=actor,
                error=error,
            )
            if error is None:
                self._record_offense(updated)
            return updated

    async def deny(self, action_id: int, actor: str) -> ActionRecord:
        async with self._lock(action_id):
            record = await self._load(action_id)
            if record.status is Status.SUGGESTED:
                return await self._update(action_id, status=Status.IGNORED, actor=actor)
            if record.status is not Status.PENDING:
                raise AlreadyResolved(f"action {action_id} is {record.status.value}")
            if record.hold_applied:
                await self._executor.unban(record.user_id)
            return await self._update(action_id, status=Status.DENIED, actor=actor)

    async def undo(self, action_id: int, actor: str) -> ActionRecord:
        async with self._lock(action_id):
            record = await self._load(action_id)
            if record.status is not Status.DONE:
                raise AlreadyResolved(f"action {action_id} is {record.status.value}")
            if record.kind in (Kind.TIMEOUT, Kind.BAN_REQUEST):
                await self._executor.unban(record.user_id)
            updated = await self._update(action_id, status=Status.UNDONE, actor=actor)
            self._offenses.undo(action_id)
            return updated

    async def expire_overdue(self) -> list[ActionRecord]:
        hold_s = self._live.settings.hold_timeout_s
        now = self._clock()
        expired: list[ActionRecord] = []
        pending = await self._store.list_actions(status=Status.PENDING, limit=_EXPIRE_BATCH)
        for candidate in pending:
            if candidate.created_at + hold_s >= now:
                continue
            async with self._lock(candidate.id):
                current = await self._load(candidate.id)
                if current.status is not Status.PENDING:
                    continue
                updated = await self._update(candidate.id, status=Status.EXPIRED)
            self._bus.publish(BanRequestReminder(updated))
            expired.append(updated)
        return expired
