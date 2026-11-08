"""Mods' remote access (spec 5.9): invites, checking who's really a mod, and the tunnel.

The tunnel and its gateway run only while someone could use them: a paired mod or a live
invite. Twitch sign-ins only prove who someone is. They're read once and never kept.
"""

import asyncio
import hashlib
import hmac
import logging
import secrets
import socket
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from aiohttp import web

from motwi.api.pairing import LABEL_MAX, Pairing
from motwi.events import ExtensionChanged, StatusChanged
from motwi.service import FieldError, Motwi, NotConnected
from motwi.store import ClientRecord
from motwi.tunnel import TunnelLike
from motwi.twitch import client_id as twitch_client_id
from motwi.twitch.auth import AuthExpired, validate
from motwi.twitch.helix import HelixError

log = logging.getLogger(__name__)

INVITE_TTL_S = 86_400
RECHECK_S = 600
TICK_S = 60
TUNNEL_WAIT_S = 75  # a new address takes a while to appear in DNS; see tunnel.py
NONCE_MAX = 128
JOIN_PATH = "/motwi-join"

Identify = Callable[[str], Awaitable[tuple[str, str]]]


class JoinRefused(Exception):
    """This person can't join; the message is for them."""


@dataclass
class _Invite:
    id: str
    code: str
    expires_at: float


def proof_for(token_hash: str, origin: str, nonce: str) -> str:
    """What only the real Motwi can answer: it holds the token's hash, and the extension can
    work it out from the token. The origin stops another address relaying the answer."""
    return hmac.new(token_hash.encode(), f"{origin}|{nonce}".encode(), hashlib.sha256).hexdigest()


class ModAccess:
    def __init__(
        self,
        motwi: Motwi,
        pairing: Pairing,
        *,
        tunnel: TunnelLike,
        make_gateway: Callable[["ModAccess"], web.Application],
        identify: Identify | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._motwi = motwi
        self._pairing = pairing
        self._tunnel = tunnel
        self._make_gateway = make_gateway
        self._identify = identify or self._identify_with_twitch
        self._clock = clock
        self._invites: dict[str, _Invite] = {}
        self._runner: web.AppRunner | None = None
        self._port: int | None = None
        self._loop_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._shared_url: str | None = None
        self._known_url: str | None = None
        self._joined_here: set[str] = set()  # mods who already got this address, by joining
        self._next_check = 0.0
        self._check_problem = ""
        self._tasks: set[asyncio.Task] = set()
        tunnel.on_change = self._tunnel_changed

    # lifecycle

    async def start(self) -> None:
        self._loop_task = asyncio.create_task(self._loop())
        await self._sync()

    async def close(self) -> None:
        if self._loop_task is not None:
            self._loop_task.cancel()
            await asyncio.gather(self._loop_task, return_exceptions=True)
        await self.settled()
        await self._stop_serving()

    def _soon(self, job) -> None:
        """Run a job outside the request that asked for it. The gateway can't stop itself from
        inside one of its own handlers, and a socket closing mustn't cut a removal short."""
        task = asyncio.create_task(job)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def settled(self) -> None:
        while self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _sync(self) -> None:
        """Run the gateway and tunnel exactly while they're needed."""
        async with self._lock:
            wanted = bool(self._live_invites()) or bool(await self._pairing.clients("mod"))
            if wanted and self._runner is None:
                await self._start_serving()
            elif not wanted and self._runner is not None:
                await self._stop_serving()

    async def _start_serving(self) -> None:
        runner = web.AppRunner(self._make_gateway(self), access_log=None, handle_signals=False)
        await runner.setup()
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self._port = sock.getsockname()[1]
        await web.SockSite(runner, sock).start()
        self._runner = runner
        await self._tunnel.start(self._port)

    async def _stop_serving(self) -> None:
        await self._tunnel.stop()
        runner, self._runner = self._runner, None
        self._port = None
        self._shared_url = None
        if runner is not None:
            await runner.cleanup()

    def _tunnel_changed(self) -> None:
        if self._tunnel.url != self._known_url:
            self._known_url = self._tunnel.url
            self._joined_here.clear()
        self._motwi.bus.publish(ExtensionChanged())
        self._motwi.bus.publish(StatusChanged())

    async def _loop(self) -> None:
        while True:
            try:
                await self._tick()
            except Exception:
                log.exception("mod access housekeeping failed")
            await asyncio.sleep(TICK_S)

    async def _tick(self) -> None:
        before = len(self._invites)
        self._live_invites()
        if len(self._invites) != before:
            await self._sync()
            self._motwi.bus.publish(ExtensionChanged())
        if self._clock() >= self._next_check:
            self._next_check = self._clock() + RECHECK_S
            await self.recheck()

    # what the window sees

    @property
    def link(self) -> str | None:
        """The address link: moves mods who are already paired, and is useless to anyone else."""
        url = self._tunnel.url
        return None if url is None else f"{url}{JOIN_PATH}"

    def can_invite(self) -> bool:
        return self._motwi.can_check_mods()

    def _live_invites(self) -> list[_Invite]:
        now = self._clock()
        for code, invite in list(self._invites.items()):
            if invite.expires_at <= now:
                del self._invites[code]
        return list(self._invites.values())

    def _invite_view(self, invite: _Invite) -> dict:
        link = self.link
        return {
            "id": invite.id,
            "url": None if link is None else f"{link}#c={invite.code}",
            "expires_at": invite.expires_at,
        }

    async def view(self) -> dict:
        mods = await self._pairing.clients("mod")
        return {
            "can_invite": self.can_invite(),
            "tunnel": {
                "state": self._tunnel.state,
                "detail": self._tunnel.detail,
            },
            "link": self.link,
            "needs_sharing": self._needs_sharing(mods),
            "invites": [self._invite_view(i) for i in self._live_invites()],
            "mods": mods,
            "check_problem": self._check_problem,
        }

    def _needs_sharing(self, mods: list[dict]) -> bool:
        return (
            self.link is not None
            and self._shared_url != self._tunnel.url
            and any(not m["connected"] and m["id"] not in self._joined_here for m in mods)
        )

    async def warnings(self) -> list[dict]:
        if not self._needs_sharing(await self._pairing.clients("mod")):
            return []
        return [
            {
                "kind": "mods",
                "level": "info",
                "text": "Motwi's address changed, so your mods need the new link.",
            }
        ]

    def mark_shared(self) -> None:
        self._shared_url = self._tunnel.url
        self._motwi.bus.publish(ExtensionChanged())

    # invites

    async def invite(self) -> dict:
        if not self.can_invite():
            raise FieldError(
                "twitch", "Reconnect Twitch first, so Motwi can check who your mods are."
            )
        code = secrets.token_urlsafe(32)
        invite = _Invite(secrets.token_hex(4), code, self._clock() + INVITE_TTL_S)
        self._invites[code] = invite
        await self._sync()
        await self._wait_for_address()
        self._motwi.bus.publish(ExtensionChanged())
        return self._invite_view(invite)

    async def _wait_for_address(self) -> None:
        deadline = self._clock() + TUNNEL_WAIT_S
        while self._tunnel.url is None and self._tunnel.state != "failed":
            if self._clock() >= deadline:
                break
            await asyncio.sleep(0.2)

    async def cancel_invite(self, invite_id: str) -> None:
        match = next((c for c, i in self._invites.items() if i.id == invite_id), None)
        if match is None:
            raise KeyError(invite_id)
        del self._invites[match]
        await self._sync()
        self._motwi.bus.publish(ExtensionChanged())

    # joining

    async def _identify_with_twitch(self, twitch_token: str) -> tuple[str, str]:
        async with httpx.AsyncClient(timeout=15) as http:
            try:
                info = await validate(http, twitch_token)
            except AuthExpired as e:
                raise JoinRefused("Twitch didn't accept that sign-in. Try again.") from e
            except httpx.HTTPError as e:
                raise JoinRefused("Motwi couldn't reach Twitch. Try again.") from e
        # a token issued to some other app says nothing about who is asking here
        if info.get("client_id") != twitch_client_id():
            raise JoinRefused("That sign-in didn't come from Motwi.")
        return str(info["user_id"]), str(info["login"])

    async def join(self, code: str, twitch_token: str, label: str) -> tuple[ClientRecord, str]:
        # Nothing here burns the invite until the person checks out: a leaked link stays
        # useless to others, and still works for the mod it was meant for.
        self._live_invites()
        if code not in self._invites:
            raise JoinRefused("This invite was already used, or it expired. Ask for a new one.")
        user_id, login = await self._identify(twitch_token)
        channel = self._motwi.streamer_login or "this channel"
        try:
            is_mod = await self._motwi.is_channel_mod(user_id)
        except (HelixError, AuthExpired, NotConnected) as e:
            log.warning("couldn't check a joining mod: %s", e)
            raise JoinRefused("Motwi couldn't check with Twitch right now. Try again.") from e
        if not is_mod:
            raise JoinRefused(
                f"{login} isn't a moderator of {channel}. Ask them to type /mod {login} in their "
                "chat, then try again."
            )
        if self._invites.pop(code, None) is None:
            raise JoinRefused("This invite was just used. Ask for a new one.")
        name = " ".join(label.split())[:LABEL_MAX] or "Browser"
        for old in await self._pairing.clients("mod"):
            if old["user_id"] == user_id and old["label"] == name:
                await self._pairing.revoke(old["id"])
        client, token = await self._pairing.add_mod(name, user_id, login)
        self._joined_here.add(client.id)
        await self._sync()
        log.info("%s joined as a mod", login)
        return client, token

    async def remove(self, client_id: str) -> None:
        await self._pairing.revoke(client_id)
        self._soon(self._sync())

    # checking they're still a mod

    async def recheck(self) -> None:
        """Only a clear no removes anyone. Twitch being unreachable says nothing about them."""
        problem = ""
        for mod in await self._pairing.clients("mod"):
            try:
                await self._check_one(mod["id"], mod["user_id"], mod["login"])
            except (HelixError, AuthExpired, NotConnected) as e:
                log.warning("couldn't check %s: %s", mod["login"], e)
                problem = "Motwi can't check your mods with Twitch right now. Is Twitch connected?"
                break
        if problem != self._check_problem:
            self._check_problem = problem
            self._motwi.bus.publish(ExtensionChanged())

    async def _check_one(self, client_id: str, user_id: str, login: str) -> None:
        if not await self._motwi.is_channel_mod(user_id):
            log.info("%s isn't a mod anymore; removing their access", login)
            await self.remove(client_id)

    def check_on_connect(self, record: ClientRecord) -> None:
        """A mod connecting after a while: the periodic check may not have reached them yet."""
        self._soon(self._check_connecting(record))

    async def _check_connecting(self, record: ClientRecord) -> None:
        try:
            await self._check_one(record.id, record.user_id or "", record.login or "")
        except (HelixError, AuthExpired, NotConnected) as e:
            log.warning("couldn't check %s on connect: %s", record.login, e)

    # the gateway's questions

    def allowed_hosts(self) -> set[str]:
        hosts = {f"127.0.0.1:{self._port}"}
        if self._tunnel.url:
            hosts.add(urlsplit(self._tunnel.url).netloc)
        return hosts

    def origin_for(self, host: str) -> str:
        url = self._tunnel.url
        if url is not None and urlsplit(url).netloc == host:
            return url
        return f"http://{host}"

    def hello(self, host: str, client: str | None, nonce: str | None) -> dict:
        out: dict = {"app": "motwi", "channel": self._motwi.streamer_login}
        if client and nonce and len(nonce) <= NONCE_MAX:
            record = self._pairing.mod_by_id(client)
            out["known"] = record is not None
            if record is not None:
                out["proof"] = proof_for(record.token_hash, self.origin_for(host), nonce)
        return out

    def present(self, *, viewer: str | None) -> list[str]:
        """Who has Motwi open in their chat right now, not counting whoever is asking."""
        names = self._pairing.connected_mods()
        if viewer is not None and self._pairing.has_connected_browser():
            names = [self._motwi.streamer_login or "streamer", *names]
        return [n for n in dict.fromkeys(names) if n != viewer]
