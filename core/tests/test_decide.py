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


# rules that do one thing, whatever the score

FIXED = {
    **RULES,
    "t": Rule("t", "No insults", Ceiling.ALWAYS_TIMEOUT, "community"),
    "b": Rule("b", "No slurs", Ceiling.ALWAYS_BAN, "community"),
}
SHORT_LADDER = (LadderStep(Kind.WARN), LadderStep(Kind.TIMEOUT, 60), LadderStep(Kind.BAN_REQUEST))


def fixed(score, rule, last=None, leeway=0.0, ladder=DEFAULT_LADDER):
    return decide_ai(
        v(score, rule),
        rules=FIXED,
        thresholds=STD,
        ladder=ladder,
        last_index=last,
        vip_leeway=leeway,
    )


def test_always_ban_skips_the_ladder_at_any_breaking_score():
    assert fixed(6.0, "b") == Plan(Kind.BAN_REQUEST, True, None, 3, 3)
    assert fixed(9.9, "b") == Plan(Kind.BAN_REQUEST, True, None, 3, 3)


def test_always_timeout_skips_the_warning():
    assert fixed(6.0, "t") == Plan(Kind.TIMEOUT, True, 60, 1, 1)


def test_always_timeout_uses_longer_lengths_but_never_bans():
    assert fixed(6.0, "t", last=1) == Plan(Kind.TIMEOUT, True, 600, 2, 2)
    assert fixed(6.0, "t", last=3) == Plan(Kind.TIMEOUT, True, 600, 2, 3)
    assert fixed(9.9, "t") == Plan(Kind.TIMEOUT, True, 600, 2, 3)


def test_fixed_rules_still_need_a_breaking_score():
    assert fixed(5.9, "t") == NO_ACTION
    assert fixed(5.9, "b") == NO_ACTION


def test_fixed_rules_keep_vip_leeway():
    assert fixed(6.5, "b", leeway=1.0) == NO_ACTION
    assert fixed(7.0, "b", leeway=1.0).kind is Kind.BAN_REQUEST


@pytest.mark.parametrize(
    "rule,expected",
    [
        ("r3", (Kind.WARN, None)),
        ("t", (Kind.TIMEOUT, 60)),
        ("b", (Kind.BAN_REQUEST, None)),
    ],
)
def test_a_nine_does_what_the_rule_says(rule, expected):
    plan = fixed(9.0, rule, ladder=SHORT_LADDER)
    assert (plan.kind, plan.duration_s) == expected


def test_fixed_rules_work_on_a_ladder_without_that_step():
    warn_only = (LadderStep(Kind.WARN),)
    assert fixed(6.0, "b", ladder=warn_only) == Plan(Kind.BAN_REQUEST, True, None, 0, 0)
    assert fixed(6.0, "t", ladder=warn_only) == Plan(Kind.TIMEOUT, True, 600, 0, 0)


def test_fixed_rules_count_toward_later_escalation():
    plan = fixed(6.0, "t")
    assert d(6.0, last=plan.level) == Plan(Kind.TIMEOUT, True, 600, 2, 2)


@pytest.mark.parametrize(
    "ceiling,kind,duration",
    [
        (Ceiling.WARN, Kind.WARN, None),
        (Ceiling.ALWAYS_TIMEOUT, Kind.TIMEOUT, 60),
        (Ceiling.ALWAYS_BAN, Kind.BAN_REQUEST, None),
    ],
)
def test_blocked_word_with_a_fixed_action_does_it_on_the_first_hit(ceiling, kind, duration):
    plan = decide_filter(base_index=-1, ceiling=ceiling, ladder=DEFAULT_LADDER, last_index=None)
    assert (plan.kind, plan.duration_s) == (kind, duration)


def test_warn_rule_never_lands_on_a_timeout_earlier_on_the_ladder():
    ladder = (LadderStep(Kind.TIMEOUT, 60), LadderStep(Kind.WARN))
    assert fixed(6.0, "r3", ladder=ladder).kind is Kind.WARN
