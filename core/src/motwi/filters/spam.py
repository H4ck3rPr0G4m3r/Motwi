"""Spam filter types."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SpamConfig:
    flood_count: int = 6
    flood_window_s: float = 5.0
    repeat_count: int = 3
    repeat_window_s: float = 30.0
    repeat_min_len: int = 10
    repeat_similarity: float = 0.9
    char_run: int = 60
    mass_mentions: int = 5
    history_size: int = 6
