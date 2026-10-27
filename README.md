# Motwi

AI-assisted Twitch chat moderation that runs on your PC and lives inside Twitch chat.

Rewrite of [Twitch-Ai-moderator](https://github.com/H4ck3rPr0G4m3r/Twitch-Ai-moderator). Work in progress.

## Development

The core engine lives in `core/` and is managed with [uv](https://docs.astral.sh/uv/).

```sh
cd core && uv sync && uv run pytest
```

## Try the engine

The simulator replays a recorded chat script through the real pipeline (filters, AI scoring, ladder, ban-request flow) with a fake Twitch that prints what it would do. No account or network is needed with the built-in fake scorer.

```sh
cd core
uv run motwi simulate fixtures/sim/demo.jsonl --ai fake --speed 0
```

Options:

- `--speed N` plays the script at N times real time. `0` replays instantly (the default is `1`).
- `--suggest-only` records suggestions instead of acting, so nothing is executed.
- `--ai env` scores with a real model instead of the fake one. Set `MOTWI_AI_KEY`, and optionally `MOTWI_AI_PROVIDER` (`gemini`, `openrouter` or `custom`), `MOTWI_AI_MODEL` and `MOTWI_AI_BASE_URL`. Keys are only ever read from the environment.

Each action prints one line, `[kind] login: text (source, score, reason)`, followed by the Twitch calls it made. A summary and the AI usage totals come last. Scripts are JSON Lines: `{"t": 1.5, "user": "name", "text": "hi", "badges": ["moderator"]}`, with an optional `emote_free_text`.
