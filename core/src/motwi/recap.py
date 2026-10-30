"""The post-stream recap (spec 5.10): built in code from what was recorded, with no AI."""

from collections.abc import Mapping

from motwi.models import Kind, Rule, Source, Status
from motwi.store import Store, StreamRecord

TOP_TRIGGERS = 5
TOP_CHATTERS = 5


def kind_totals(counts: Mapping[tuple[Kind, Status], int]) -> dict[str, dict[str, int]]:
    counted = {
        Status.PENDING,
        Status.DONE,
        Status.FAILED,
        Status.UNDONE,
        Status.DENIED,
        Status.EXPIRED,
    }
    out = {
        k: {"count": 0, "undone": 0, "denied": 0, "failed": 0, "suggested": 0}
        for k in ("delete", "warn", "timeout", "ban_request")
    }
    for (kind, status), n in counts.items():
        row = out.get(kind.value)
        if row is None:
            continue
        if status in counted:
            row["count"] += n
        if status is Status.UNDONE:
            row["undone"] += n
        elif status is Status.DENIED:
            row["denied"] += n
        elif status is Status.FAILED:
            row["failed"] += n
        elif status is Status.SUGGESTED:
            row["suggested"] += n
    return out


def trigger_label(source: Source, rule_id: str | None, rules: Mapping[str, Rule]) -> str:
    if source is Source.SPAM:
        return "Spam filter"
    if source is Source.BLOCKED_WORD:
        return "Blocked words"
    if rule_id is None:
        return "No specific rule"
    rule = rules.get(rule_id)
    if rule is None:
        return "A deleted rule"
    return f"{rule.text} (stream rule)" if rule.scope == "stream" else rule.text


async def build_recap(store: Store, stream: StreamRecord, *, seen_ending: bool = True) -> dict:
    """A snapshot of one stream. `stream` must have ended. Rule text is copied in, because
    this-stream rules are cleared right after and the recap has to outlive them."""
    ended = stream.ended_at if stream.ended_at is not None else stream.started_at
    rules = {r.id: r for r in await store.list_rules()}
    triggers: dict[str, int] = {}
    for source, rule_id, n in await store.triggers(stream_id=stream.id):
        label = trigger_label(source, rule_id, rules)
        triggers[label] = triggers.get(label, 0) + n
    usage = await store.usage_totals(stream_id=stream.id)
    return {
        "stream_id": stream.id,
        "title": stream.title,
        "game": stream.game,
        "started_at": stream.started_at,
        "ended_at": ended,
        "seen_ending": seen_ending,
        "checked": await store.count_checked(stream_id=stream.id),
        "unchecked": await store.count_unchecked(since=stream.started_at, until=ended),
        "actions": kind_totals(await store.action_counts(stream_id=stream.id)),
        "triggers": sorted(
            ({"label": k, "count": v} for k, v in triggers.items()), key=lambda t: -t["count"]
        )[:TOP_TRIGGERS],
        "escalated": await store.top_offenders(stream.id, limit=TOP_CHATTERS),
        "cost": usage.cost_usd,
        "ai_requests": usage.requests,
        "outages": await store.outages_between(stream.started_at, ended),
    }


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def _span(seconds: float) -> str:
    minutes = round(seconds / 60)
    if minutes < 1:
        return "under a minute"
    if minutes < 60:
        return _plural(minutes, "minute")
    return f"{minutes / 60:.1f} hours"


def recap_summary(recap: Mapping) -> str:
    """One notification's worth of recap."""
    actions = recap["actions"]

    def standing(kind: str) -> int:
        t = actions[kind]
        return t["count"] - t["undone"] - t["denied"] - t["failed"]

    done = [
        (standing("delete"), "deleted"),
        (standing("warn"), "warned"),
        (standing("timeout"), "timed out"),
        (standing("ban_request"), "banned"),
    ]
    parts = [f"{n:,} {word}" for n, word in done if n > 0]
    text = f"{_plural(recap['checked'], 'message')} checked. "
    text += ", ".join(parts) + "." if parts else "No actions."
    down = sum(o["ended_at"] - o["started_at"] for o in recap["outages"] if o["state"] == "down")
    if down > 0:
        text += f" The AI was down for {_span(down)}."
    text += f" AI cost ${recap['cost']:.2f}."
    return text
