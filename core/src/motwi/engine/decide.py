"""Pure decision logic: verdict or filter hit -> moderation plan."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from motwi.models import Ceiling, Kind, LadderStep, Rule, Thresholds, Verdict


@dataclass(frozen=True)
class Plan:
    kind: Kind
    delete: bool
    duration_s: int | None
    ladder_index: int | None  # the ladder step executed (after the ceiling), -1 = delete only
    level: int | None = None  # escalation position this offense earned (before the ceiling)


NO_ACTION = Plan(Kind.NONE, False, None, None)

_DEFAULT_CEILING = Ceiling.TIMEOUT
_FALLBACK_TIMEOUT_S = 600  # a timeout rule on a ladder that has no timeout step


def _first_index(ladder: Sequence[LadderStep], kind: Kind) -> int | None:
    return next((i for i, step in enumerate(ladder) if step.kind is kind), None)


def _last_index_of(ladder: Sequence[LadderStep], kinds: set[Kind]) -> int:
    return max((i for i, step in enumerate(ladder) if step.kind in kinds), default=-1)


def _cap(ladder: Sequence[LadderStep], ceiling: Ceiling) -> int:
    """Highest ladder index the ceiling permits; -1 means delete only."""
    match ceiling:
        case Ceiling.DELETE:
            return -1
        case Ceiling.WARN:
            return _last_index_of(ladder, {Kind.WARN})
        case Ceiling.TIMEOUT:
            return _last_index_of(ladder, {Kind.WARN, Kind.TIMEOUT})
        case _:
            return len(ladder) - 1


def _always_ban(ladder: Sequence[LadderStep], level: int) -> Plan:
    idx = _first_index(ladder, Kind.BAN_REQUEST)
    if idx is None:
        idx = len(ladder) - 1
    return Plan(Kind.BAN_REQUEST, True, None, idx, max(level, idx))


def _always_timeout(ladder: Sequence[LadderStep], level: int) -> Plan:
    """The longest timeout this offense has earned, and never less than the shortest."""
    timeouts = [i for i, step in enumerate(ladder) if step.kind is Kind.TIMEOUT]
    if not timeouts:
        idx = max(level, 0)
        return Plan(Kind.TIMEOUT, True, _FALLBACK_TIMEOUT_S, idx, idx)
    idx = max((i for i in timeouts if i <= level), default=timeouts[0])
    return Plan(Kind.TIMEOUT, True, ladder[idx].duration_s, idx, max(level, idx))


def _resolve(
    base: int, ceiling: Ceiling, ladder: Sequence[LadderStep], last_index: int | None
) -> Plan:
    """Every offense moves the chatter one step up; the ceiling only caps what is executed.

    `last_index` is the level of the chatter's previous offense, so capped offenses (a
    warn-only rule, a delete-only blocked word) still count toward escalation (spec 5.3).
    """
    level = base if last_index is None else max(base, last_index + 1)
    level = min(level, len(ladder) - 1)
    match ceiling:
        case Ceiling.ALWAYS_BAN:
            return _always_ban(ladder, level)
        case Ceiling.ALWAYS_TIMEOUT:
            return _always_timeout(ladder, level)
        case Ceiling.WARN:
            idx = _cap(ladder, ceiling)
        case _:
            idx = min(level, _cap(ladder, ceiling))
    if idx < 0:
        return Plan(Kind.DELETE, True, None, -1, level)
    step = ladder[idx]
    return Plan(step.kind, True, step.duration_s, idx, level)


def decide_ai(
    verdict: Verdict,
    *,
    rules: Mapping[str, Rule],
    thresholds: Thresholds,
    ladder: Sequence[LadderStep],
    last_index: int | None,
    vip_leeway: float = 0.0,
) -> Plan:
    warn = thresholds.warn + vip_leeway
    timeout = thresholds.timeout + vip_leeway
    ban = thresholds.ban + vip_leeway
    score = verdict.score
    if score < warn:
        return NO_ACTION

    if score >= ban:
        base = _first_index(ladder, Kind.BAN_REQUEST)
        if base is None:
            base = _first_index(ladder, Kind.TIMEOUT)
    elif score >= timeout:
        base = _first_index(ladder, Kind.TIMEOUT)
    else:
        base = 0
    if base is None:
        base = len(ladder) - 1

    rule = rules.get(verdict.rule_id) if verdict.rule_id is not None else None
    ceiling = rule.ceiling if rule is not None else _DEFAULT_CEILING
    return _resolve(base, ceiling, ladder, last_index)


def decide_filter(
    *,
    base_index: int,
    ceiling: Ceiling,
    ladder: Sequence[LadderStep],
    last_index: int | None,
) -> Plan:
    return _resolve(base_index, ceiling, ladder, last_index)
