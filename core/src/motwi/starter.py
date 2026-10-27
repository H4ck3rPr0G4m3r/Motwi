"""Default community rules offered at setup."""

from motwi.models import Ceiling

STARTER_RULES: list[tuple[str, Ceiling]] = [
    ("No hate speech, slurs, or targeted harassment", Ceiling.BAN),
    ("No self-promotion or links to other channels", Ceiling.DELETE),
    ("Don't insult or bully other chatters", Ceiling.TIMEOUT),
    ("No sexual or explicit content", Ceiling.TIMEOUT),
    ("No backseating unless the streamer asks for help", Ceiling.WARN),
]
