# Motwi

AI-assisted Twitch chat moderation that runs on your PC and lives inside Twitch chat.

Rewrite of [Twitch-Ai-moderator](https://github.com/H4ck3rPr0G4m3r/Twitch-Ai-moderator). Work in progress.

## Development

The core engine lives in `core/` and is managed with [uv](https://docs.astral.sh/uv/).

```sh
cd core && uv sync && uv run pytest
```
