"""`motwi login` and `motwi run`: sign-in and live moderation of a real channel."""

import asyncio
import webbrowser
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from typing import Literal, TextIO

import httpx

from motwi.app import MotwiApp, ai_clients, print_events, resolve_ai, seed_if_empty, stream_context
from motwi.live import LiveConfig
from motwi.paths import db_path
from motwi.store import Store
from motwi.twitch import BOT_SCOPES, SCOPES, client_id
from motwi.twitch.auth import TwitchAuth, poll_device_flow, start_device_flow
from motwi.twitch.eventsub import EventSubClient, Socket
from motwi.twitch.executor import HelixExecutor
from motwi.twitch.helix import HelixClient
from motwi.twitch.tokens import Role, SecretStore, Token, TokenStore, ai_key_name

Connect = Callable[[str], Awaitable[Socket]]


async def _ws_connect(url: str) -> Socket:
    from websockets.asyncio.client import connect

    return await connect(url, open_timeout=10, max_size=2**20)


def subscriptions(broadcaster_id: str, moderator_id: str) -> list[tuple[str, str, dict]]:
    b = {"broadcaster_user_id": broadcaster_id}
    return [
        ("channel.chat.message", "1", {**b, "user_id": moderator_id}),
        ("channel.update", "2", b),
        ("stream.online", "1", b),
        ("stream.offline", "1", b),
    ]


async def ensure_moderator(helix: HelixClient, bot: Token, channel: Token) -> None:
    channels = await helix.moderated_channels(bot.user_id)
    if not any(c.get("broadcaster_id") == channel.user_id for c in channels):
        raise ValueError(
            f"{bot.login} is not a moderator in {channel.login}'s chat. "
            f"Type /mod {bot.login} in your chat, then try again."
        )


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
    streamer = tokens.load("streamer")
    if role == "bot" and streamer is None:
        raise ValueError("connect your own account first: motwi login")
    async with httpx.AsyncClient(timeout=15) as http:
        code = await start_device_flow(http, cid, scopes)
        print(f"Open {code.verification_uri} and enter the code {code.user_code}", file=out)
        print("Waiting for you to approve Motwi on Twitch...", file=out)
        out.flush()
        open_browser(code.verification_uri)
        token = await poll_device_flow(http, cid, scopes, code)
        if role == "bot" and streamer is not None:
            if token.user_id == streamer.user_id:
                raise ValueError("that is your own account; sign in with the bot account instead")
            tokens.save("bot", token)
            bot_helix = HelixClient(
                TwitchAuth(http=http, client_id=cid, store=tokens, role="bot"), http
            )
            try:
                await ensure_moderator(bot_helix, token, streamer)
            except ValueError as e:
                print(f"Signed in as {token.login}, but: {e}", file=out)
                return token
        else:
            tokens.save(role, token)
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
    """Moderate the streamer's channel until cancelled."""
    cid = client_id()
    tokens = TokenStore(secrets)
    async with httpx.AsyncClient(timeout=10) as http, httpx.AsyncClient() as ai_http:
        streamer = TwitchAuth(http=http, client_id=cid, store=tokens, role="streamer")
        bot = (
            TwitchAuth(http=http, client_id=cid, store=tokens, role="bot")
            if tokens.load("bot") is not None
            else None
        )
        mod_auth = bot or streamer
        helix = HelixClient(mod_auth, http)
        channel_helix = HelixClient(streamer, http) if bot else helix
        broadcaster_id = streamer.token.user_id
        if bot is not None:
            await ensure_moderator(helix, bot.token, streamer.token)

        store = await Store.open(db_path())
        app: MotwiApp | None = None
        tasks: list[asyncio.Task] = []
        try:
            await seed_if_empty(store)
            live = await LiveConfig.load(store)
            settings, keys = resolve_ai(live.settings, env, secrets)
            live.settings = replace(settings, suggest_only=settings.suggest_only or suggest_only)
            live.stream = stream_context(
                await channel_helix.get_channel(broadcaster_id),
                await channel_helix.get_stream(broadcaster_id),
            )
            app = MotwiApp(
                store=store,
                live=live,
                executor=HelixExecutor(helix, broadcaster_id=broadcaster_id),
                clients=ai_clients(keys, ai_http),
                self_user_ids={mod_auth.token.user_id},
            )

            async def subscribe(session_id: str) -> None:
                for sub_type, version, condition in subscriptions(
                    broadcaster_id, mod_auth.token.user_id
                ):
                    await helix.subscribe(sub_type, version, condition, session_id)

            def on_state(state: str, detail: str) -> None:
                print(f"[twitch {state}] {detail}".rstrip(), file=out)
                out.flush()

            eventsub = EventSubClient(
                connect=connect, subscribe=subscribe, on_event=app.on_event, on_state=on_state
            )
            mode = " in suggest-only mode" if live.settings.suggest_only else ""
            print(
                f"Motwi is watching #{streamer.token.login} as {mod_auth.token.login}{mode}. "
                "Press Ctrl+C to stop.",
                file=out,
            )
            out.flush()
            tasks = [
                asyncio.create_task(print_events(app.bus, out)),
                asyncio.create_task(app.housekeeping()),
                asyncio.create_task(eventsub.run()),
            ]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            for t in done:
                t.result()
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if app is not None:
                await app.aclose()
            await store.close()


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
