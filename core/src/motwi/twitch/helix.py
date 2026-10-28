"""Small Helix client: the endpoints Motwi needs, with refresh-on-401 and rate-limit waits."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import httpx

from motwi.twitch.auth import TwitchAuth

log = logging.getLogger(__name__)

HELIX = "https://api.twitch.tv/helix"
MAX_ATTEMPTS = 3
MAX_RATE_WAIT_S = 60.0


class HelixError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"Twitch error {status}: {message}" if status else message)
        self.status = status
        self.message = message


def _message(resp: httpx.Response) -> str:
    try:
        return str(resp.json().get("message") or resp.reason_phrase)
    except ValueError:
        return resp.text or resp.reason_phrase


class HelixClient:
    def __init__(
        self,
        auth: TwitchAuth,
        http: httpx.AsyncClient,
        *,
        max_concurrent: int = 4,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._auth = auth
        self._http = http
        self._sem = asyncio.Semaphore(max_concurrent)
        self._sleep = sleep
        self._clock = clock

    @property
    def user_id(self) -> str:
        """The account this client acts as."""
        return self._auth.token.user_id

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | list[tuple[str, str]] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        async with self._sem:
            refreshed = False
            for _ in range(MAX_ATTEMPTS):
                token = await self._auth.access_token()
                headers = {"Client-Id": self._auth.client_id, "Authorization": f"Bearer {token}"}
                try:
                    resp = await self._http.request(
                        method, HELIX + path, params=params, json=json, headers=headers
                    )
                except httpx.TransportError as e:
                    raise HelixError(0, f"could not reach Twitch ({type(e).__name__})") from e
                if resp.status_code == 401 and not refreshed:
                    await self._auth.refresh(token)
                    refreshed = True
                    continue
                if resp.status_code == 429:
                    reset = float(resp.headers.get("Ratelimit-Reset", self._clock() + 1))
                    wait = min(MAX_RATE_WAIT_S, max(0.5, reset - self._clock()))
                    log.warning("Helix rate limit hit on %s, waiting %.1fs", path, wait)
                    await self._sleep(wait)
                    continue
                if resp.is_success:
                    return resp.json() if resp.content else None
                raise HelixError(resp.status_code, _message(resp))
            raise HelixError(429, "Twitch rate limit, gave up after retries")

    async def _data(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        body = await self.request("GET", path, params=params)
        return list((body or {}).get("data", []))

    # reads

    async def get_users(self, *, logins: Sequence[str] = (), ids: Sequence[str] = ()) -> list[dict]:
        params = [("login", x) for x in logins] + [("id", x) for x in ids]
        body = await self.request("GET", "/users", params=params or None)
        return list((body or {}).get("data", []))

    async def get_channel(self, broadcaster_id: str) -> dict | None:
        data = await self._data("/channels", {"broadcaster_id": broadcaster_id})
        return data[0] if data else None

    async def get_stream(self, user_id: str) -> dict | None:
        data = await self._data("/streams", {"user_id": user_id})
        return data[0] if data else None

    async def moderated_channels(self, user_id: str) -> list[dict]:
        channels: list[dict] = []
        cursor: str | None = None
        while True:
            params = {"user_id": user_id, "first": 100, **({"after": cursor} if cursor else {})}
            body = await self.request("GET", "/moderation/channels", params=params) or {}
            channels += body.get("data", [])
            cursor = (body.get("pagination") or {}).get("cursor")
            if not cursor:
                return channels

    # moderation

    async def delete_message(self, broadcaster_id: str, moderator_id: str, message_id: str) -> None:
        await self.request(
            "DELETE",
            "/moderation/chat",
            params={
                "broadcaster_id": broadcaster_id,
                "moderator_id": moderator_id,
                "message_id": message_id,
            },
        )

    async def ban(
        self,
        broadcaster_id: str,
        moderator_id: str,
        user_id: str,
        reason: str,
        duration_s: int | None = None,
    ) -> None:
        data: dict[str, Any] = {"user_id": user_id, "reason": reason}
        if duration_s is not None:
            data["duration"] = duration_s
        await self.request(
            "POST",
            "/moderation/bans",
            params={"broadcaster_id": broadcaster_id, "moderator_id": moderator_id},
            json={"data": data},
        )

    async def unban(self, broadcaster_id: str, moderator_id: str, user_id: str) -> None:
        await self.request(
            "DELETE",
            "/moderation/bans",
            params={
                "broadcaster_id": broadcaster_id,
                "moderator_id": moderator_id,
                "user_id": user_id,
            },
        )

    async def warn(self, broadcaster_id: str, moderator_id: str, user_id: str, reason: str) -> None:
        await self.request(
            "POST",
            "/moderation/warnings",
            params={"broadcaster_id": broadcaster_id, "moderator_id": moderator_id},
            json={"data": {"user_id": user_id, "reason": reason}},
        )

    async def send_chat(self, broadcaster_id: str, sender_id: str, message: str) -> None:
        body = await self.request(
            "POST",
            "/chat/messages",
            json={"broadcaster_id": broadcaster_id, "sender_id": sender_id, "message": message},
        )
        result = ((body or {}).get("data") or [{}])[0]
        if result.get("is_sent") is False:
            drop = result.get("drop_reason") or {}
            raise HelixError(200, f"message not sent: {drop.get('message', 'dropped by Twitch')}")

    # eventsub

    async def subscribe(
        self, sub_type: str, version: str, condition: dict[str, str], session_id: str
    ) -> None:
        await self.request(
            "POST",
            "/eventsub/subscriptions",
            json={
                "type": sub_type,
                "version": version,
                "condition": condition,
                "transport": {"method": "websocket", "session_id": session_id},
            },
        )
