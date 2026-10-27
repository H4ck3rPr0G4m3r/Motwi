"""Parses the LLM's JSON reply into verdicts, defensively."""

import json
import math
import re

from motwi.ai.prompt import BuiltRequest
from motwi.models import Verdict

_FENCE = re.compile(r"```[a-zA-Z0-9_-]*\s*(.*?)```", re.DOTALL)
MAX_REASON = 200


class ParseError(Exception):
    """The reply was not usable JSON of the expected shape."""


def _strip_fences(text: str) -> str:
    text = text.strip()
    match = _FENCE.search(text)
    if match:
        return match.group(1).strip()
    # an unterminated opening fence
    return re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text).strip()


def parse_verdicts(text: str, req: BuiltRequest) -> dict[str, Verdict]:
    try:
        data = json.loads(_strip_fences(text))
    except ValueError as e:
        raise ParseError("reply is not valid JSON") from e

    items = data.get("results") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ParseError("reply has no results list")

    out: dict[str, Verdict] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        real_id = req.id_map.get(item.get("id")) if isinstance(item.get("id"), str) else None
        if real_id is None or real_id in out:
            continue
        score = item.get("score")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or math.isnan(score):
            continue
        rule = item.get("rule")
        rule_id = req.rule_map.get(rule) if isinstance(rule, str) else None
        reason = item.get("reason")
        reason = reason.strip()[:MAX_REASON] if isinstance(reason, str) else ""
        out[real_id] = Verdict(real_id, float(min(10.0, max(0.0, score))), rule_id, reason)
    return out
