"""`motwi login` and `motwi run`: sign-in and live moderation of a real channel."""

import asyncio
import webbrowser
from collections.abc import Callable, Mapping
from typing import Literal, TextIO

import httpx

from motwi.app import print_events, resolve_ai
from motwi.events import StatusChanged
from motwi.paths import db_path
from motwi.service import Connect, Motwi, _ws_connect, subscriptions
from motwi.store import Store
from motwi.twitch import BOT_SCOPES, SCOPES, client_id
from motwi.twitch.accounts import save_sign_in
from motwi.twitch.auth import AuthExpired, poll_device_flow, start_device_flow
from motwi.twitch.tokens import Role, SecretStore, Token, TokenStore, ai_key_name

__all__ = ["login", "logout", "run_live", "set_key", "subscriptions"]


async def login(
    role: Role,
    *,
    secrets: SecretStore,
    out: TextIO,
    open_browser: Callable[[str], object] = webbrowser.open,
) -> Token:
    cid = client_id()
    scopes = BOT_SCOPES if role == "bot" else SCOPES
    tokens = TokenStore(secrets)
    if role == "bot" and tokens.load("streamer") is None:
        raise ValueError("connect your own account first: motwi login")
    async with httpx.AsyncClient(timeout=15) as http:
        code = await start_device_flow(http, cid, scopes)
        print(f"Open {code.verification_uri} and enter the code {code.user_code}", file=out)
        print("Waiting for you to approve Motwi on Twitch...", file=out)
        out.flush()
        open_browser(code.verification_uri)
        token = await poll_device_flow(http, cid, scopes, code)
        warning = await save_sign_in(role, token, tokens=tokens, http=http, client_id=cid)
    if warning:
        print(f"Signed in as {token.login}, but: {warning}", file=out)
    else:
        print(f"Signed in as {token.login}.", file=out)
    return token


async def run_live(
    *,
    secrets: SecretStore,
    env: Mapping[str, str],
    out: TextIO,
    suggest_only: bool = False,
    connect: Connect = _ws_connect,
) -> None:
    """Moderate the streamer's channel headless until cancelled."""
    client_id()  # fail early without a client ID
    tokens = TokenStore(secrets)
    if tokens.load("streamer") is None:
        raise AuthExpired("no Twitch streamer account connected")
    store = await Store.open(db_path())
    motwi: Motwi | None = None
    printer: asyncio.Task | None = None
    try:
        overrides: dict = {"setup_complete": True, "paused": False}
        if suggest_only:
            overrides["suggest_only"] = True
        motwi = await Motwi.open(
            store, secrets=secrets, env=env, connect=connect, overrides=overrides
        )
        resolve_ai(motwi.live.settings, env, secrets)  # the headless run needs an AI key
        printer = asyncio.create_task(
            _print(motwi, out, suggest_only=motwi.live.settings.suggest_only)
        )
        await motwi.connect()
        await motwi.wait_session()
        state, detail = motwi.twitch_state, motwi.twitch_detail
        if state == "reconnect":
            raise AuthExpired(detail or "Twitch sign-in expired")
        if state == "error":
            raise ValueError(detail)
    finally:
        if printer is not None:
            printer.cancel()
            await asyncio.gather(printer, return_exceptions=True)
        if motwi is not None:
            await motwi.aclose()
        await store.close()


async def _print(motwi: Motwi, out: TextIO, *, suggest_only: bool) -> None:
    q = motwi.bus.subscribe()
    announced = False
    events = asyncio.create_task(print_events(motwi.bus, out))
    try:
        while True:
            e = await q.get()
            if not isinstance(e, StatusChanged):
                continue
            state, detail = motwi.twitch_state, motwi.twitch_detail
            if state == "connected" and not announced:
                announced = True
                mode = " in suggest-only mode" if suggest_only else ""
                print(
                    f"Motwi is watching #{motwi.streamer_login} as {motwi.moderator_login}{mode}. "
                    "Press Ctrl+C to stop.",
                    file=out,
                )
            elif state != "connected":
                announced = False
                print(f"[twitch {state}] {detail}".rstrip(), file=out)
            out.flush()
    finally:
        events.cancel()
        motwi.bus.unsubscribe(q)


def logout(role: Role, secrets: SecretStore) -> bool:
    tokens = TokenStore(secrets)
    existed = tokens.load(role) is not None
    tokens.delete(role)
    return existed


def set_key(slot: Literal["primary", "backup"], key: str, secrets: SecretStore) -> None:
    key = key.strip()
    if not key:
        raise ValueError("the key is empty")
    secrets.set(ai_key_name(slot), key)
