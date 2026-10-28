import pytest

from motwi.engine.decide import NO_ACTION, Plan, decide_ai, decide_filter
from motwi.models import (
    DEFAULT_LADDER,
    PRESETS,
    Ceiling,
    Kind,
    LadderStep,
    Rule,
    Verdict,
)

STD = PRESETS["standard"]
RULES = {
    "r1": Rule("r1", "No hate", Ceiling.BAN, "community"),
    "r2": Rule("r2", "No spoilers", Ceiling.DELETE, "stream"),
    "r3": Rule("r3", "No backseat", Ceiling.WARN, "community"),
}


def v(score, rule=None):
    return Verdict("m", score, rule, "x")


def d(score, rule=None, last=None, leeway=0.0, ladder=DEFAULT_LADDER):
    return decide_ai(
        v(score, rule),
        rules=RULES,
        thresholds=STD,
        ladder=ladder,
        last_index=last,
        vip_leeway=leeway,
    )


def test_below_warn_no_action():
    assert d(5.9, "r1") == NO_ACTION


def test_warn():
    assert d(6.0) == Plan(Kind.WARN, True, None, 0, 0)


def test_timeout_level():
    assert d(7.6) == Plan(Kind.TIMEOUT, True, 60, 1, 1)


def test_ban_needs_ban_rule():
    assert d(9.5, "r1").kind is Kind.BAN_REQUEST
    assert d(9.5) == Plan(Kind.TIMEOUT, True, 600, 2, 3)


def test_unknown_rule_treated_as_no_rule():
    assert d(9.5, "gone") == Plan(Kind.TIMEOUT, True, 600, 2, 3)


def test_delete_ceiling():
    assert d(9.9, "r2") == Plan(Kind.DELETE, True, None, -1, 3)


def test_warn_ceiling_never_escalates_past_warn():
    assert d(8.0, "r3", last=2).kind is Kind.WARN


def test_escalation_steps_up():
    assert d(6.1, "r1", last=0) == Plan(Kind.TIMEOUT, True, 60, 1, 1)


def test_escalation_reaches_ban_only_with_ban_rule():
    assert d(6.1, "r1", last=2).kind is Kind.BAN_REQUEST
    assert d(6.1, None, last=2) == Plan(Kind.TIMEOUT, True, 600, 2, 3)


def test_escalation_clamps_at_top_of_ladder():
    assert d(6.1, "r1", last=3) == Plan(Kind.BAN_REQUEST, True, None, 3, 3)


def test_high_score_is_not_lowered_by_low_history():
    assert d(7.6, last=-1) == Plan(Kind.TIMEOUT, True, 60, 1, 1)


def test_vip_leeway():
    assert d(6.5, leeway=1.0) == NO_ACTION
    assert d(7.0, leeway=1.0) == Plan(Kind.WARN, True, None, 0, 0)


def test_vip_leeway_shifts_all_thresholds():
    assert d(9.4, "r1", leeway=1.0).ladder_index == 1
    assert d(10.0, "r1", leeway=1.0).kind is Kind.BAN_REQUEST


def test_filter_first_hit_delete_only():
    assert decide_filter(
        base_index=-1, ceiling=Ceiling.TIMEOUT, ladder=DEFAULT_LADDER, last_index=None
    ) == Plan(Kind.DELETE, True, None, -1, -1)


def test_filter_repeat_escalates():
    assert (
        decide_filter(
            base_index=-1, ceiling=Ceiling.TIMEOUT, ladder=DEFAULT_LADDER, last_index=-1
        ).kind
        is Kind.WARN
    )


def test_filter_base_index_and_ceiling():
    kw = {"ladder": DEFAULT_LADDER, "last_index": None}
    assert decide_filter(base_index=1, ceiling=Ceiling.BAN, **kw) == Plan(
        Kind.TIMEOUT, True, 60, 1, 1
    )
    assert decide_filter(base_index=3, ceiling=Ceiling.TIMEOUT, **kw).ladder_index == 2
    assert decide_filter(base_index=3, ceiling=Ceiling.BAN, **kw).kind is Kind.BAN_REQUEST
    assert decide_filter(base_index=9, ceiling=Ceiling.BAN, **kw).ladder_index == 3
    assert decide_filter(base_index=2, ceiling=Ceiling.DELETE, **kw) == Plan(
        Kind.DELETE, True, None, -1, 2
    )


@pytest.mark.parametrize(
    "ladder,ceiling,expected",
    [
        ((LadderStep(Kind.TIMEOUT, 60), LadderStep(Kind.BAN_REQUEST)), Ceiling.WARN, -1),
        ((LadderStep(Kind.WARN), LadderStep(Kind.BAN_REQUEST)), Ceiling.TIMEOUT, 0),
        ((LadderStep(Kind.BAN_REQUEST),), Ceiling.TIMEOUT, -1),
    ],
)
def test_ceiling_with_unusual_ladders(ladder, ceiling, expected):
    plan = decide_filter(base_index=5, ceiling=ceiling, ladder=ladder, last_index=None)
    assert plan.ladder_index == expected


def test_capped_offenses_still_move_the_chatter_up():
    # three warn-only backseat offenses, then an insult: the insult lands three steps up
    first = d(6.5, "r3")
    second = d(6.5, "r3", last=first.level)
    third = d(6.5, "r3", last=second.level)
    assert [p.kind for p in (first, second, third)] == [Kind.WARN] * 3
    assert d(6.5, last=third.level) == Plan(Kind.TIMEOUT, True, 600, 2, 3)


def test_delete_only_hit_never_lowers_escalation():
    plan = decide_filter(base_index=-1, ceiling=Ceiling.DELETE, ladder=DEFAULT_LADDER, last_index=2)
    assert (plan.kind, plan.level) == (Kind.DELETE, 3)
    assert d(6.5, last=plan.level) == Plan(Kind.TIMEOUT, True, 600, 2, 3)
