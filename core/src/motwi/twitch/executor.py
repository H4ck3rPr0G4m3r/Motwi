"""The real Executor: moderation actions through Helix as the moderator account."""

from collections.abc import Awaitable

from motwi.moderation import ExecutorError
from motwi.twitch.helix import HelixClient, HelixError

TEXT_LIMIT = 500


def _cap(text: str) -> str:
    return text if len(text) <= TEXT_LIMIT else text[: TEXT_LIMIT - 1] + "…"


class HelixExecutor:
    def __init__(self, helix: HelixClient, *, broadcaster_id: str) -> None:
        self._helix = helix
        self._b = broadcaster_id

    @property
    def _m(self) -> str:
        return self._helix.user_id

    async def _run(self, call: Awaitable[None], *, ok_if: str | None = None, ok_404=False) -> None:
        """Run a Helix call; errors whose desired end state already holds count as success."""
        try:
            await call
        except HelixError as e:
            if ok_404 and e.status == 404:
                return
            if ok_if is not None and ok_if in e.message.lower():
                return
            raise ExecutorError(str(e)) from e

    async def delete_message(self, message_id: str) -> None:
        await self._run(self._helix.delete_message(self._b, self._m, message_id), ok_404=True)

    async def warn(self, user_id: str, reason: str) -> None:
        await self._run(self._helix.warn(self._b, self._m, user_id, _cap(reason)))

    async def send_chat(self, text: str) -> None:
        await self._run(self._helix.send_chat(self._b, self._m, _cap(text)))

    async def timeout(self, user_id: str, seconds: int, reason: str) -> None:
        await self._run(
            self._helix.ban(self._b, self._m, user_id, _cap(reason), seconds),
            ok_if="already banned",
        )

    async def ban(self, user_id: str, reason: str) -> None:
        await self._run(
            self._helix.ban(self._b, self._m, user_id, _cap(reason)), ok_if="already banned"
        )

    async def unban(self, user_id: str) -> None:
        await self._run(self._helix.unban(self._b, self._m, user_id), ok_if="not banned")
