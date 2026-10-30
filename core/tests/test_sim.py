import io
import json
from pathlib import Path

import pytest

from motwi.ai.client import Completion
from motwi.cli import main
from motwi.models import Kind, Status
from motwi.sim.fake_llm import SLUR_PLACEHOLDER, FakeLLMClient
from motwi.sim.run import simulate
from motwi.starter import STARTER_RULES

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sim" / "demo.jsonl"


async def test_demo_script_outcomes():
    out = io.StringIO()
    s = await simulate(FIXTURE, ai="fake", speed=0, suggest_only=False, out=out)
    assert s.counts[Kind.BAN_REQUEST] == 1  # xx_v0id_xx; its second message is merged
    assert s.counts[Kind.DELETE] >= 2  # spoiler + link spam first hit
    assert s.counts[Kind.TIMEOUT] >= 1  # Grizzlecat escalates
    assert s.unchecked == 0
    text = out.getvalue()
    assert "[ban_request] xx_v0id_xx:" in text
    assert "pixelbandit_:" in text
    assert "Summary:" in text and "AI usage:" in text
    for exempt in ("themod_", "streamer_", "mxzz", "kofi_draws"):
        assert f"] {exempt}:" not in text


async def test_suggest_only_executes_nothing():
    out = io.StringIO()
    s = await simulate(FIXTURE, ai="fake", speed=0, suggest_only=True, out=out)
    assert s.executor_calls == 0
    assert "    ->" not in out.getvalue()
    assert set(s.statuses) == {Status.SUGGESTED}
    assert sum(s.statuses.values()) > 0


async def test_env_mode_without_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("MOTWI_AI_KEY", raising=False)
    with pytest.raises(ValueError, match="MOTWI_AI_KEY"):
        await simulate(FIXTURE, ai="env", speed=0, suggest_only=False, out=io.StringIO())


async def test_fake_llm_scores_by_keyword():
    rules = [
        {"id": "r1", "scope": "community", "rule": STARTER_RULES[0][0]},
        {"id": "r2", "scope": "community", "rule": STARTER_RULES[2][0]},
        {"id": "r3", "scope": "stream", "rule": "No spoilers for the game"},
    ]
    texts = {
        "m1": f"you {SLUR_PLACEHOLDER}",
        "m2": "ur so trash",
        "m3": "he dies in the ending",
        "m4": "nice trash can in the background",
        "m5": "hello",
    }
    document = {
        "rules": rules,
        "messages": [{"id": k, "user": "x", "text": v} for k, v in texts.items()],
    }
    reply = await FakeLLMClient().complete(
        [{"role": "system", "content": ""}, {"role": "user", "content": json.dumps(document)}],
        timeout_s=1,
    )
    assert isinstance(reply, Completion) and reply.cost_usd == 0.0
    got = {r["id"]: (r["score"], r["rule"]) for r in json.loads(reply.text)["results"]}
    assert got == {
        "m1": (9.6, "r1"),
        "m2": (7.6, "r2"),
        "m3": (8.1, "r3"),
        "m4": (1.0, None),
        "m5": (1.0, None),
    }


def test_cli_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "simulate" in capsys.readouterr().out


def test_cli_without_subcommand_opens_the_app(monkeypatch):
    opened = []
    monkeypatch.setattr("motwi.desktop.run_app", lambda **_: opened.append(1) or 0)
    assert main([]) == 0
    assert opened == [1]


def test_cli_simulate_runs(capsys):
    assert main(["simulate", str(FIXTURE), "--ai", "fake", "--speed", "0"]) == 0
    assert "Summary:" in capsys.readouterr().out


def test_cli_missing_file(capsys):
    assert main(["simulate", "nope.jsonl"]) == 2
    assert "no such file" in capsys.readouterr().err
