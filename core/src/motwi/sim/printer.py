"""Console output for the simulator: an executor that prints, plus line formatters."""

from contextvars import ContextVar
from typing import TextIO

from motwi.store import ActionRecord, UsageTotals

# When set (per task), executor calls are collected here instead of printed immediately, so
# concurrent messages can print their calls grouped under their own action line.
captured_calls: ContextVar[list[str] | None] = ContextVar("captured_calls", default=None)


class PrintingExecutor:
    """Stands in for Twitch: prints every call instead of making it."""

    def __init__(self, out: TextIO) -> None:
        self._out = out
        self.calls = 0

    def _log(self, line: str) -> None:
        self.calls += 1
        text = f"    -> {line}"
        buffer = captured_calls.get()
        if buffer is None:
            print(text, file=self._out)
        else:
            buffer.append(text)

    async def delete_message(self, message_id: str) -> None:
        self._log(f"delete message {message_id}")

    async def warn(self, user_id: str, reason: str) -> None:
        self._log(f"warn {user_id}: {reason}")

    async def send_chat(self, text: str) -> None:
        self._log(f"chat: {text}")

    async def timeout(self, user_id: str, seconds: int, reason: str) -> None:
        self._log(f"timeout {user_id} for {seconds}s: {reason}")

    async def ban(self, user_id: str, reason: str) -> None:
        self._log(f"ban {user_id}: {reason}")

    async def unban(self, user_id: str) -> None:
        self._log(f"unban {user_id}")


def format_action(a: ActionRecord) -> str:
    score = "-" if a.score is None else f"{a.score:g}"
    return (
        f"[{a.kind.value:<11}] {a.user_login}: {a.message_text[:60]}  "
        f"({a.source.value}, score {score}, {a.reason})"
    )


def format_usage(totals: UsageTotals) -> str:
    return (
        f"AI usage: {totals.requests} requests, {totals.messages} messages, "
        f"{totals.prompt_tokens} prompt + {totals.completion_tokens} completion tokens, "
        f"${totals.cost_usd:.4f}"
    )
