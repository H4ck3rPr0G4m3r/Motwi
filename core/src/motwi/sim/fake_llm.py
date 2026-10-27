"""Deterministic keyword scorer that stands in for a real LLM in the simulator."""

import json
import re

from motwi.ai.client import Completion

SLUR_PLACEHOLDER = "████"
_WORDS = re.compile(r"[a-z0-9']+")
_INSULTS = {"uninstall", "trash", "braindead"}
_AIMED = {"ur", "you", "youre", "you're", "u"}


def _rule_id(rules: list[dict], *, keyword: str, scope: str | None = None) -> str | None:
    for rule in rules:
        if keyword in rule["rule"].lower() and (scope is None or rule["scope"] == scope):
            return rule["id"]
    return None


def _score(text: str, rules: list[dict]) -> tuple[float, str | None, str]:
    lowered = text.lower()
    words = set(_WORDS.findall(lowered))
    if SLUR_PLACEHOLDER in text or "kys" in words:
        return 9.6, _rule_id(rules, keyword="hate"), "hateful or threatening language"
    if words & _INSULTS and words & _AIMED:
        return 7.6, _rule_id(rules, keyword="insult"), "insulting another person"
    if words & {"dies", "ending"}:
        rule = _rule_id(rules, keyword="spoiler", scope="stream")
        if rule is not None:
            return 8.1, rule, "spoils the story"
    return 1.0, None, "fine"


class FakeLLMClient:
    provider = "fake"
    model = "fake-llm"

    async def complete(self, messages: list[dict], *, timeout_s: float) -> Completion:
        document = json.loads(messages[1]["content"])
        rules = document["rules"]
        results = []
        for message in document["messages"]:
            score, rule, reason = _score(message["text"], rules)
            results.append({"id": message["id"], "score": score, "rule": rule, "reason": reason})
        text = json.dumps({"results": results})
        return Completion(
            text=text,
            prompt_tokens=len(messages[1]["content"]) // 4,
            completion_tokens=len(text) // 4,
            cost_usd=0.0,
            provider=self.provider,
            model=self.model,
        )
