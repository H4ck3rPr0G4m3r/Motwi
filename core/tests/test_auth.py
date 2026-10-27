import asyncio

import httpx
import pytest
import respx

from motwi.twitch.auth import (
    ID_BASE,
    AuthError,
    AuthExpired,
    DeviceCode,
    TwitchAuth,
    poll_device_flow,
    start_device_flow,
)
from motwi.twitch.tokens import MemorySecrets, Token, TokenStore

CODE = DeviceCode("dev", "ABCD-1234", "https://www.twitch.tv/activate", 1800, 5)
VALIDATE = {
    "client_id": "cid",
    "login": "streamer",
    "user_id": "42",
    "scopes": [],
    "expires_in": 14000,
}
GRANT = {
    "access_token": "acc2",
    "refresh_token": "ref2",
    "expires_in": 14400,
    "scope": ["user:read:chat"],
}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.now += s


def _error(message: str) -> httpx.Response:
    return httpx.Response(400, json={"status": 400, "message": message})


@respx.mock
async def test_start_device_flow_sends_scopes():
    route = respx.post(f"{ID_BASE}/device").respond(
        200,
        json={
            "device_code": "dev",
            "user_code": "ABCD-1234",
            "verification_uri": "u",
            "expires_in": 1800,
            "interval": 5,
        },
    )
    async with httpx.AsyncClient() as http:
        code = await start_device_flow(http, "cid", ["user:read:chat", "user:write:chat"])
    assert code.user_code == "ABCD-1234"
    assert b"scopes=user%3Aread%3Achat+user%3Awrite%3Achat" in route.calls[0].request.content


@respx.mock
async def test_poll_waits_through_pending_and_slow_down():
    respx.post(f"{ID_BASE}/token").mock(
        side_effect=[
            _error("authorization_pending"),
            _error("slow_down"),
            httpx.Response(200, json=GRANT),
        ]
    )
    respx.get(f"{ID_BASE}/validate").respond(200, json=VALIDATE)
    clock = Clock()
    async with httpx.AsyncClient() as http:
        token = await poll_device_flow(http, "cid", ["s"], CODE, sleep=clock.sleep, clock=clock)
    assert clock.sleeps == [5, 5, 10]
    assert (token.login, token.user_id, token.access_token) == ("streamer", "42", "acc2")
    assert token.expires_at == clock.now + 14400


@respx.mock
async def test_poll_reports_expired_code():
    respx.post(f"{ID_BASE}/token").mock(return_value=_error("invalid device code"))
    clock = Clock()
    async with httpx.AsyncClient() as http:
        with pytest.raises(AuthError, match="expired"):
            await poll_device_flow(http, "cid", ["s"], CODE, sleep=clock.sleep, clock=clock)


@respx.mock
async def test_poll_gives_up_at_deadline():
    respx.post(f"{ID_BASE}/token").mock(return_value=_error("authorization_pending"))
    clock = Clock()
    short = DeviceCode("dev", "X", "u", 12, 5)
    async with httpx.AsyncClient() as http:
        with pytest.raises(AuthError, match="expired"):
            await poll_device_flow(http, "cid", ["s"], short, sleep=clock.sleep, clock=clock)
    assert len(clock.sleeps) == 3


def _auth(http, clock, expires_at=100_000.0):
    store = TokenStore(MemorySecrets())
    store.save("streamer", Token("acc1", "ref1", expires_at, (), "42", "streamer"))
    return TwitchAuth(http=http, client_id="cid", store=store, role="streamer", clock=clock), store


@respx.mock
async def test_refresh_persists_rotated_token_and_runs_once_for_concurrent_callers():
    route = respx.post(f"{ID_BASE}/token").respond(200, json=GRANT)
    respx.get(f"{ID_BASE}/validate").respond(200, json=VALIDATE)
    async with httpx.AsyncClient() as http:
        auth, store = _auth(http, Clock())
        results = await asyncio.gather(auth.refresh("acc1"), auth.refresh("acc1"))
    assert results == ["acc2", "acc2"]
    assert route.call_count == 1
    assert store.load("streamer").refresh_token == "ref2"


@respx.mock
async def test_access_token_refreshes_near_expiry():
    respx.post(f"{ID_BASE}/token").respond(200, json=GRANT)
    respx.get(f"{ID_BASE}/validate").respond(200, json=VALIDATE)
    clock = Clock()
    async with httpx.AsyncClient() as http:
        auth, _ = _auth(http, clock, expires_at=clock.now + 60)
        assert await auth.access_token() == "acc2"


@respx.mock
async def test_failed_refresh_raises_auth_expired_and_keeps_old_token():
    respx.post(f"{ID_BASE}/token").mock(return_value=_error("Invalid refresh token"))
    async with httpx.AsyncClient() as http:
        auth, store = _auth(http, Clock())
        with pytest.raises(AuthExpired):
            await auth.refresh()
    assert store.load("streamer").refresh_token == "ref1"


def test_missing_account_raises_auth_expired():
    with pytest.raises(AuthExpired):
        TwitchAuth(http=None, client_id="cid", store=TokenStore(MemorySecrets()), role="bot")
