"""Everything that talks to Twitch: auth, Helix, EventSub."""

import os

# Motwi's public Twitch app (device code flow). Not a secret, so it ships in the code;
# MOTWI_TWITCH_CLIENT_ID overrides it for development.
CLIENT_ID = "fo0j62netc6u54f0wqf4khruex5exy"

_ACTING: tuple[str, ...] = (
    "user:read:chat",
    "user:write:chat",
    "moderator:manage:banned_users",
    "moderator:manage:chat_messages",
    "moderator:manage:warnings",
)
# moderation:read lets Motwi check who really mods the channel, before and after a mod joins
CHECK_MODS_SCOPE = "moderation:read"
SCOPES: tuple[str, ...] = (*_ACTING, CHECK_MODS_SCOPE)
BOT_SCOPES: tuple[str, ...] = (*_ACTING, "user:read:moderated_channels")


def client_id() -> str:
    cid = os.environ.get("MOTWI_TWITCH_CLIENT_ID") or CLIENT_ID
    if not cid:
        raise ValueError(
            "no Twitch client ID: register the Motwi app on dev.twitch.tv or set "
            "MOTWI_TWITCH_CLIENT_ID"
        )
    return cid
