"""Everything that talks to Twitch: auth, Helix, EventSub."""

import os

# Motwi's public Twitch app (device code flow). Not a secret. Empty until the app is registered;
# MOTWI_TWITCH_CLIENT_ID overrides it for development.
CLIENT_ID = ""

SCOPES: tuple[str, ...] = (
    "user:read:chat",
    "user:write:chat",
    "moderator:manage:banned_users",
    "moderator:manage:chat_messages",
    "moderator:manage:warnings",
)
BOT_SCOPES: tuple[str, ...] = (*SCOPES, "user:read:moderated_channels")


def client_id() -> str:
    cid = os.environ.get("MOTWI_TWITCH_CLIENT_ID") or CLIENT_ID
    if not cid:
        raise ValueError(
            "no Twitch client ID: register the Motwi app on dev.twitch.tv or set "
            "MOTWI_TWITCH_CLIENT_ID"
        )
    return cid
