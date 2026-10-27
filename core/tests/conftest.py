"""Shared test helpers."""

from dataclasses import replace

import pytest

from motwi.models import Kind, Source, Status
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


@pytest.fixture
async def store():
    s = await Store.open(":memory:")
    yield s
    await s.close()
