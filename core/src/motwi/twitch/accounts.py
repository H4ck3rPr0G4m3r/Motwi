"""Connecting the streamer and bot accounts after a device code sign-in."""

import httpx

from motwi.twitch.auth import TwitchAuth
from motwi.twitch.helix import HelixClient
from motwi.twitch.tokens import Role, Token, TokenStore


async def ensure_moderator(helix: HelixClient, bot: Token, channel: Token) -> None:
    channels = await helix.moderated_channels(bot.user_id)
    if not any(c.get("broadcaster_id") == channel.user_id for c in channels):
        raise ValueError(
            f"{bot.login} is not a moderator in {channel.login}'s chat. "
            f"Type /mod {bot.login} in your chat, then try again."
        )


async def save_sign_in(
    role: Role, token: Token, *, tokens: TokenStore, http: httpx.AsyncClient, client_id: str
) -> str | None:
    """Store a fresh sign-in. Returns a warning to show, or None when all is well.

    Raises ValueError when the account can't be used for this role at all.
    """
    if role == "streamer":
        tokens.save("streamer", token)
        return None
    streamer = tokens.load("streamer")
    if streamer is None:
        raise ValueError("connect your own account first")
    if token.user_id == streamer.user_id:
        raise ValueError("that is your own account; sign in with the bot account instead")
    tokens.save("bot", token)
    helix = HelixClient(TwitchAuth(http=http, client_id=client_id, store=tokens, role="bot"), http)
    try:
        await ensure_moderator(helix, token, streamer)
    except ValueError as e:
        return str(e)
    return None
