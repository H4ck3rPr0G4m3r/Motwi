"""Device Code Grant flow and token refresh for Motwi's public Twitch app (no client secret)."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

import httpx

from motwi.twitch.tokens import Role, Token, TokenStore

log = logging.getLogger(__name__)

ID_BASE = "https://id.twitch.tv/oauth2"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
REFRESH_MARGIN_S = 300


class AuthError(Exception):
    """Sign-in did not complete (denied, expired, or rejected)."""


class AuthExpired(Exception):
    """The stored token can no longer be refreshed; the user must sign in again."""


@dataclass(frozen=True)
class DeviceCode:
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


def _message(resp: httpx.Response) -> str:
    try:
        return str(resp.json().get("message", resp.text))
    except ValueError:
        return resp.text


async def start_device_flow(
    http: httpx.AsyncClient, client_id: str, scopes: Sequence[str]
) -> DeviceCode:
    resp = await http.post(
        f"{ID_BASE}/device", data={"client_id": client_id, "scopes": " ".join(scopes)}
    )
    if resp.status_code != 200:
        raise AuthError(f"Twitch refused the sign-in request: {_message(resp)}")
    d = resp.json()
    return DeviceCode(
        d["device_code"], d["user_code"], d["verification_uri"], d["expires_in"], d["interval"]
    )


async def validate(http: httpx.AsyncClient, access_token: str) -> dict:
    resp = await http.get(f"{ID_BASE}/validate", headers={"Authorization": f"OAuth {access_token}"})
    if resp.status_code == 401:
        raise AuthExpired("Twitch token is no longer valid")
    resp.raise_for_status()
    return resp.json()


async def _token_from(http: httpx.AsyncClient, payload: dict, clock: Callable[[], float]) -> Token:
    info = await validate(http, payload["access_token"])
    return Token(
        access_token=payload["access_token"],
        refresh_token=payload["refresh_token"],
        expires_at=clock() + float(payload.get("expires_in") or info.get("expires_in", 0)),
        scopes=tuple(payload.get("scope") or info.get("scopes") or ()),
        user_id=str(info["user_id"]),
        login=str(info["login"]),
    )


async def poll_device_flow(
    http: httpx.AsyncClient,
    client_id: str,
    scopes: Sequence[str],
    code: DeviceCode,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.time,
) -> Token:
    """Wait for the user to approve the code at twitch.tv/activate."""
    deadline = clock() + code.expires_in
    interval = max(1, code.interval)
    while clock() < deadline:
        await sleep(interval)
        resp = await http.post(
            f"{ID_BASE}/token",
            data={
                "client_id": client_id,
                "scopes": " ".join(scopes),
                "device_code": code.device_code,
                "grant_type": DEVICE_GRANT,
            },
        )
        if resp.status_code == 200:
            return await _token_from(http, resp.json(), clock)
        message = _message(resp)
        if message == "authorization_pending":
            continue
        if message == "slow_down":
            interval += 5
            continue
        if "invalid device code" in message.lower():
            raise AuthError("the sign-in code expired; start again")
        raise AuthError(f"sign-in failed: {message}")
    raise AuthError("the sign-in code expired; start again")


async def refresh_token(
    http: httpx.AsyncClient,
    client_id: str,
    token: Token,
    clock: Callable[[], float] = time.time,
) -> Token:
    resp = await http.post(
        f"{ID_BASE}/token",
        data={
            "client_id": client_id,
            "grant_type": "refresh_token",
            "refresh_token": token.refresh_token,
        },
    )
    if resp.status_code in (400, 401):
        raise AuthExpired(f"Twitch refused to refresh the token: {_message(resp)}")
    resp.raise_for_status()
    return await _token_from(http, resp.json(), clock)


class TwitchAuth:
    """The live token for one account. Refreshes early, once, and persists every rotation."""

    def __init__(
        self,
        *,
        http: httpx.AsyncClient,
        client_id: str,
        store: TokenStore,
        role: Role,
        clock: Callable[[], float] = time.time,
    ) -> None:
        token = store.load(role)
        if token is None:
            raise AuthExpired(f"no Twitch {role} account connected")
        self._http = http
        self._client_id = client_id
        self._store = store
        self._role = role
        self._clock = clock
        self._token = token
        self._lock = asyncio.Lock()

    @property
    def token(self) -> Token:
        return self._token

    @property
    def client_id(self) -> str:
        return self._client_id

    async def access_token(self) -> str:
        if self._token.expires_at - self._clock() < REFRESH_MARGIN_S:
            return await self.refresh(self._token.access_token)
        return self._token.access_token

    async def refresh(self, stale: str | None = None) -> str:
        """Refresh unless another caller already replaced the `stale` access token."""
        async with self._lock:
            if stale is not None and stale != self._token.access_token:
                return self._token.access_token
            new = await refresh_token(self._http, self._client_id, self._token, self._clock)
            self._store.save(self._role, new)  # the old refresh token is now spent
            self._token = new
            log.info("refreshed Twitch %s token", self._role)
            return new.access_token
