# Motwi

AI-assisted Twitch chat moderation that runs on your PC and lives inside Twitch chat.

Rewrite of [Twitch-Ai-moderator](https://github.com/H4ck3rPr0G4m3r/Twitch-Ai-moderator). Work in progress.

## Run the app

Motwi is a Python core (`core/`, managed with [uv](https://docs.astral.sh/uv/)) and a window UI (`ui/`, Svelte and Vite). Build the UI once, then start the app:

```sh
cd ui && npm ci && npm run build
cd ../core && uv sync && uv run motwi
```

A window opens with the first-run setup: connect Twitch, pick an AI provider (Groq is the recommended one) and paste its key, then review the starter rules. Closing the window keeps Motwi running in the tray. Windows 11 puts new tray icons under the `^` arrow; drag it out to keep it visible. Quit Motwi from that icon. Data lives in `%APPDATA%\Motwi`, and keys and Twitch tokens are kept in Windows Credential Manager.

To see Motwi inside Twitch chat, build the extension (`cd extension && npm ci && npm run build`), then follow the Browser extension steps in Settings: load `extension/dist` as an unpacked extension in Chrome, Edge, Brave or Opera, and press Connect this browser. It activates only on your own channel's chat.

Other commands: `motwi run` moderates headless in the terminal, and `motwi login`, `motwi logout` and `motwi set-key` manage accounts and keys from the command line.

## Mods

Mods can answer ban requests, undo actions and act on suggestions from their own computers. They can't change rules or settings. On the Mods page, press **Invite a mod** and send them the link. They install the extension (the link offers it), sign in with Twitch, and Motwi checks with Twitch that they really mod your channel, again every 10 minutes after that. Everything they do is recorded under their name.

Motwi reaches them through a Cloudflare quick tunnel, so there's no account to make or port to open. It runs only while you have a mod or an open invite. The address changes whenever Motwi restarts. Motwi then shows you a new link to send them, and they don't have to sign in again.

## Development

```sh
cd core && uv run pytest && uv run ruff check .
cd ui && npm run check
cd extension && npm run check && npm test
```

The extension also has browser tests. `npm run e2e` runs the built extension in real Chromium against a real demo core, with a copy of Twitch's chat page. `npm run live` runs it on a live public Twitch chat, marking real messages for its own view only: actions go to a fake, and the browser is anonymous, so nothing reaches Twitch. Both need `extension/dist` and `ui/dist` built, and `npx playwright install chromium` once.

To work on the UI against sample data, run the demo core. It uses a throwaway data folder and never touches your real data or Twitch:

```sh
cd core && uv run python -m motwi.sim.demo          # add --setup to start at first-run setup
```

Then open the printed address. For hot reload, run `MOTWI_PORT=47900 npm run dev` in `ui/` and open `http://localhost:5173/#k=demo`. The dev server proxies the API to that core. `--channel name` sets which channel the extension treats as yours, and `--empty` starts without sample actions.

## Try the engine

The simulator replays a recorded chat script through the real pipeline (filters, AI scoring, ladder, ban-request flow) with a fake Twitch that prints what it would do. No account or network is needed with the built-in fake scorer.

```sh
cd core
uv run motwi simulate fixtures/sim/demo.jsonl --ai fake --speed 0
```

Options:

- `--speed N` plays the script at N times real time. `0` replays instantly (the default is `1`).
- `--suggest-only` records suggestions instead of acting, so nothing is executed.
- `--ai env` scores with a real model instead of the fake one. Set `MOTWI_AI_KEY`, and optionally `MOTWI_AI_PROVIDER` (`groq`, `gemini`, `openrouter` or `custom`), `MOTWI_AI_MODEL` and `MOTWI_AI_BASE_URL`. Keys are only ever read from the environment.

Each action prints one line, `[kind] login: text (source, score, reason)`, followed by the Twitch calls it made. A summary and the AI usage totals come last. Scripts are JSON Lines: `{"t": 1.5, "user": "name", "text": "hi", "badges": ["moderator"]}`, with an optional `emote_free_text`.

## Build the installer

```sh
uv run --project core --group packaging python packaging/build.py
uv run --project core python packaging/test_install.py
```

This needs Node and [Inno Setup 6](https://jrsoftware.org/isinfo.php). It writes `Motwi-Setup-<version>.exe` and the extension zip to `packaging/out`. The second command installs that build into a throwaway folder, launches it, quits it and uninstalls it. It uses its own port, data folder and Credential Manager namespace.

The installer isn't signed yet, so Windows SmartScreen warns about it. Choose More info, then Run anyway.
