import json

import httpx
import pytest
import respx

from motwi.moderation import ExecutorError
from motwi.twitch.auth import ID_BASE, TwitchAuth
from motwi.twitch.executor import HelixExecutor
from motwi.twitch.helix import HELIX, HelixClient, HelixError
from motwi.twitch.tokens import MemorySecrets, Token, TokenStore

FAR = 10_000_000_000.0


class Sleeper:
    def __init__(self) -> None:
        self.waits: list[float] = []

    async def __call__(self, s: float) -> None:
        self.waits.append(s)


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as client:
        yield client


def _helix(http, sleep=None, clock=lambda: 1000.0):
    store = TokenStore(MemorySecrets())
    store.save("bot", Token("acc1", "ref1", FAR, (), "99", "motwibot"))
    auth = TwitchAuth(http=http, client_id="cid", store=store, role="bot")
    return HelixClient(auth, http, sleep=sleep or Sleeper(), clock=clock)


def _err(status: int, message: str) -> httpx.Response:
    return httpx.Response(status, json={"error": "x", "status": status, "message": message})


@respx.mock
async def test_requests_carry_client_id_and_bearer(http):
    route = respx.get(f"{HELIX}/channels").respond(
        200, json={"data": [{"title": "t", "game_name": "g"}]}
    )
    assert (await _helix(http).get_channel("42"))["title"] == "t"
    req = route.calls[0].request
    assert req.headers["Client-Id"] == "cid"
    assert req.headers["Authorization"] == "Bearer acc1"
    assert req.url.params["broadcaster_id"] == "42"


@respx.mock
async def test_401_refreshes_once_and_retries(http):
    route = respx.get(f"{HELIX}/streams").mock(
        side_effect=[_err(401, "Invalid OAuth token"), httpx.Response(200, json={"data": []})]
    )
    respx.post(f"{ID_BASE}/token").respond(
        200, json={"access_token": "acc2", "refresh_token": "ref2", "expires_in": 14400}
    )
    respx.get(f"{ID_BASE}/validate").respond(200, json={"login": "motwibot", "user_id": "99"})
    assert await _helix(http).get_stream("42") is None
    assert route.calls[1].request.headers["Authorization"] == "Bearer acc2"


@respx.mock
async def test_429_waits_until_reset_then_retries(http):
    respx.delete(f"{HELIX}/moderation/chat").mock(
        side_effect=[
            httpx.Response(429, headers={"Ratelimit-Reset": "1003"}),
            httpx.Response(204),
        ]
    )
    sleeper = Sleeper()
    await _helix(http, sleep=sleeper).delete_message("42", "99", "m1")
    assert sleeper.waits == [3.0]


@respx.mock
async def test_errors_raise_helix_error_with_twitch_message(http):
    respx.post(f"{HELIX}/moderation/warnings").mock(return_value=_err(403, "missing scope"))
    with pytest.raises(HelixError, match="missing scope") as info:
        await _helix(http).warn("42", "99", "u1", "be nice")
    assert info.value.status == 403


@respx.mock
async def test_network_failure_is_a_helix_error(http):
    respx.get(f"{HELIX}/streams").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(HelixError, match="could not reach Twitch"):
        await _helix(http).get_stream("42")


@respx.mock
async def test_send_chat_dropped_message_is_an_error(http):
    respx.post(f"{HELIX}/chat/messages").respond(
        200,
        json={"data": [{"is_sent": False, "drop_reason": {"message": "msg_duplicate"}}]},
    )
    with pytest.raises(HelixError, match="msg_duplicate"):
        await _helix(http).send_chat("42", "99", "hi")


@respx.mock
async def test_executor_timeout_ban_and_caps_reason(http):
    route = respx.post(f"{HELIX}/moderation/bans").respond(200, json={"data": [{}]})
    ex = HelixExecutor(_helix(http), broadcaster_id="42")
    await ex.timeout("u1", 600, "Motwi: " + "x" * 600)
    await ex.ban("u1", "Motwi: slur")
    first = json.loads(route.calls[0].request.content)["data"]
    second = json.loads(route.calls[1].request.content)["data"]
    assert first["duration"] == 600 and len(first["reason"]) == 500
    assert "duration" not in second
    assert route.calls[0].request.url.params["moderator_id"] == "99"


@respx.mock
async def test_executor_tolerates_states_that_already_hold(http):
    respx.post(f"{HELIX}/moderation/bans").mock(
        return_value=_err(400, "The user specified in the user_id field is already banned.")
    )
    respx.delete(f"{HELIX}/moderation/bans").mock(
        return_value=_err(400, "The user specified in the user_id field is not banned.")
    )
    respx.delete(f"{HELIX}/moderation/chat").mock(return_value=_err(404, "not found"))
    ex = HelixExecutor(_helix(http), broadcaster_id="42")
    await ex.ban("u1", "r")
    await ex.unban("u1")
    await ex.delete_message("m1")


@respx.mock
async def test_executor_maps_other_failures_to_executor_error(http):
    respx.post(f"{HELIX}/moderation/bans").mock(return_value=_err(400, "user may not be banned"))
    ex = HelixExecutor(_helix(http), broadcaster_id="42")
    with pytest.raises(ExecutorError, match="may not be banned"):
        await ex.timeout("u1", 60, "r")


@respx.mock
async def test_moderated_channels_follows_pagination(http):
    respx.get(f"{HELIX}/moderation/channels").mock(
        side_effect=[
            httpx.Response(
                200, json={"data": [{"broadcaster_id": "1"}], "pagination": {"cursor": "c"}}
            ),
            httpx.Response(200, json={"data": [{"broadcaster_id": "42"}], "pagination": {}}),
        ]
    )
    channels = await _helix(http).moderated_channels("99")
    assert [c["broadcaster_id"] for c in channels] == ["1", "42"]
