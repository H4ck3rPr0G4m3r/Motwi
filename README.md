# Motwi

AI-assisted Twitch chat moderation that runs on your PC and lives inside Twitch chat.

Rewrite of [Twitch-Ai-moderator](https://github.com/H4ck3rPr0G4m3r/Twitch-Ai-moderator). Work in progress.

## Run the app

Motwi is a Python core (`core/`, managed with [uv](https://docs.astral.sh/uv/)) and a window UI (`ui/`, Svelte and Vite). Build the UI once, then start the app:

```sh
cd ui && npm ci && npm run build
cd ../core && uv sync && uv run motwi
```

A window opens with the first-run setup: connect Twitch, pick an AI provider and paste its key, then review the starter rules. Closing the window keeps Motwi running in the tray. Windows 11 puts new tray icons under the `^` arrow; drag it out to keep it visible. Quit Motwi from that icon. Data lives in `%APPDATA%\Motwi`, and keys and Twitch tokens are kept in Windows Credential Manager.

Other commands: `motwi run` moderates headless in the terminal, and `motwi login`, `motwi logout` and `motwi set-key` manage accounts and keys from the command line.

## Development

```sh
cd core && uv run pytest && uv run ruff check .
cd ui && npm run check
```

To work on the UI against sample data, run the demo core. It uses a throwaway data folder and never touches your real data or Twitch:

```sh
cd core && uv run python -m motwi.sim.demo          # add --setup to start at first-run setup
```

Then open the printed address. For hot reload, run `MOTWI_PORT=47900 npm run dev` in `ui/` and open `http://localhost:5173/#k=demo`. The dev server proxies the API to that core.

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
