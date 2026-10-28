"""EventSub payloads to engine types."""

from datetime import datetime
from typing import Any

from motwi.models import ChatMessage


def parse_ts(timestamp: str, fallback: float) -> float:
    try:
        return datetime.fromisoformat(timestamp).timestamp()
    except (TypeError, ValueError):
        return fallback


def chat_message(event: dict[str, Any], ts: float) -> ChatMessage | None:
    """A `channel.chat.message` event, or None for messages Motwi must not moderate.

    Shared-chat messages from another channel are skipped: those are not this community's
    chatters, and their own moderators handle them.
    """
    source = event.get("source_broadcaster_user_id")
    if source and source != event.get("broadcaster_user_id"):
        return None
    badges = {b.get("set_id", "") for b in event.get("badges") or []}
    if "lead_moderator" in badges:
        badges.add("moderator")
    message = event.get("message") or {}
    text = message.get("text") or ""
    fragments = message.get("fragments") or []
    if fragments:
        emote_free = " ".join(
            "".join(f.get("text", "") for f in fragments if f.get("type") != "emote").split()
        )
    else:
        emote_free = text
    return ChatMessage(
        id=event["message_id"],
        user_id=event["chatter_user_id"],
        user_login=event["chatter_user_login"],
        display_name=event.get("chatter_user_name") or event["chatter_user_login"],
        text=text,
        emote_free_text=emote_free,
        badges=frozenset(badges - {""}),
        ts=ts,
    )
