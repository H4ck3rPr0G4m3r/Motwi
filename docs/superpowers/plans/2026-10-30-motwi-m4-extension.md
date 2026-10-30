# Motwi M4: the browser extension

**Goal:** Motwi inside Twitch chat: status bar, inline marks, ban pop-up, suggest-only buttons, in light and dark, paired with one click from the window.

**Spec:** `docs/superpowers/specs/2026-10-27-motwi-design.md` (6.1, 5.5, 5.6, 6.3 step 5). Approved design: `mockups/chat-layout-v2.html`, with the colors from spec 6.4 and the undo wording from 5.6.

**Stops at:** the extension for the streamer's own Motwi. Mods' remote access and "who is looking" presence are M6. The zip and store listing are M5.

## Findings that shaped it

Probed on live Twitch (2026-10-30, anonymous, read-only):

- Every `.chat-line__message` has a React fiber whose parent props carry `message.id`, the same UUID EventSub sends, plus `isDeleted`. Exact matching works, but only from the page's MAIN world, since isolated worlds can't see React's expando properties.
- `data-a-user` holds the login, `[data-a-target=chat-line-message-body]` the text, and `.chat-room[data-a-target=chat-theme-*]` the chat theme.
- `.chat-room__content` is present in pop-out chat and on the channel page.
- Twitch serves no CSP on these pages.

## Design

**Core**

- Migration 4: `clients(id, kind, label, token_hash, created_at, last_seen)`. Only hashes are stored; tokens are 256-bit, so a plain SHA-256 is enough.
- `motwi/api/pairing.py`: `Pairing` holds the single live one-time code (10 minutes), redeems it for a durable token, checks tokens, revokes them, and tracks open sockets per client.
- Extension tokens are scoped to `GET /api/status`, `GET /api/chat`, `POST /api/engine` and `POST /api/actions/{id}/{verb}`, plus the event socket. Everything else is 403. Revoking closes the client's socket with 4401.
- `POST /api/pair` takes `{code, label}`, needs no bearer, and only answers `chrome-extension://` origins. No CORS headers: the extension's host permissions make them unnecessary.
- Window only: `GET /api/extension`, `POST /api/extension/pair` (opens the default browser), `POST /api/extension/reveal` (opens the folder), `DELETE /api/extension/clients/{id}`.
- `GET /api/chat`: status, this stream's (or today's) live counts, recent actions, open requests and warnings.
- `approve` takes an optional "as warn" for suggestions, for the mockup's "Warn instead" button.
- `ui/public/pair.html`: the page the browser opens. It reports whether the extension answered.
- Demo core gains a fake executor so buttons work, `--channel`, and a demo-only route to inject actions, for extension development.

**Extension (`extension/`, MV3, Svelte, Vite)**

- `worker.js`: the only code that holds the token or talks to the core. One WebSocket while any Twitch tab is on the paired channel, with a 20 s keepalive and reconnect backoff. It re-reads `/api/chat` after events instead of counting locally, so numbers can't drift.
- `page-world.js` (MAIN world, no privileges): copies `message.id` from the fiber to a `data-motwi-id` attribute. Nothing else.
- `content.js`: activates only on the paired channel, mounts the bar in a shadow root at the top of `.chat-room__content`, and mounts a mark in each acted-on line. Falls back to login + text when a line has no ID. Unmatched actions still appear in the bar's log.
- `pair.js`: the bridge on `pair.html` that redeems the code through the worker.
- Tokens are inherited CSS custom properties keyed on Twitch's theme, so light and dark need no JS.
- Nothing claims success early: buttons stay busy until the core confirms, and a lost connection replaces the status with "Motwi isn't running" right away.

**Window:** the Extension step in setup and a Settings section share `ExtensionPanel.svelte`.

## Review focus

1. A web page or another extension must not gain control: scope, origin and one-time code tests in `test_api.py`.
2. Motwi closing mid-stream: the bar must say so, not keep "Watching" (e2e).
3. Chat autoscroll must stay pinned when a mark adds height (e2e, live check).
4. Twitch re-rendering a line must not lose or duplicate its mark (e2e).
5. Two tabs open: both stay in sync and a pop-up answered in one collapses in the other (e2e).

## Verification

`uv run pytest`, `npm run check`, `npm test`, `npm run e2e` (real Chromium, real core, Twitch page fixture), and `npm run live` (real Chromium on a live public Twitch chat, with the demo core marking real messages). Stream Manager and moderator view need a signed-in owner, so they are left for a manual check.
