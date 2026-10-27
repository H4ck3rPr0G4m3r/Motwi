"""Shared test helpers."""

import asyncio
from dataclasses import replace

import pytest

from motwi.models import Kind, Source, Status
from motwi.moderation import ExecutorError
from motwi.store import NewAction, Store


def make_new_action(**overrides) -> NewAction:
    base = NewAction(
        stream_id="stream1",
        user_id="u1",
        user_login="viewer1",
        message_id="m1",
        message_text="some message",
        context=["earlier line"],
        source=Source.AI,
        rule_id=None,
        score=7.0,
        reason="rude",
        kind=Kind.WARN,
        delete=True,
        duration_s=None,
        ladder_index=0,
        status=Status.DONE,
        actor=None,
        hold_applied=False,
        error=None,
    )
    return replace(base, **overrides)


class RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.fail_next: str | None = None

    def _maybe_fail(self, name: str) -> None:
        if self.fail_next == name:
            self.fail_next = None
            raise ExecutorError(f"{name} failed")

    async def delete_message(self, message_id):
        await asyncio.sleep(0)
        self._maybe_fail("delete")
        self.calls.append(("delete", message_id))

    async def warn(self, user_id, reason):
        self._maybe_fail("warn")
        self.calls.append(("warn", user_id, reason))

    async def send_chat(self, text):
        self._maybe_fail("chat")
        self.calls.append(("chat", text))

    async def timeout(self, user_id, seconds, reason):
        await asyncio.sleep(0)
        self._maybe_fail("timeout")
        self.calls.append(("timeout", user_id, seconds, reason))

    async def ban(self, user_id, reason):
        await asyncio.sleep(0)
        self._maybe_fail("ban")
        self.calls.append(("ban", user_id, reason))

    async def unban(self, user_id):
        self._maybe_fail("unban")
        self.calls.append(("unban", user_id))


@pytest.fixture
async def store():
    s = await Store.open(":memory:")
    yield s
    await s.close()
