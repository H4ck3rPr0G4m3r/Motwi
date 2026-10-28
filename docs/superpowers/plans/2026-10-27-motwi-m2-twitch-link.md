# Motwi M2: Twitch Link Implementation Plan

> Executed inline by the orchestrator (owner's instruction), one commit per task, one review at the end.

**Goal:** `motwi login` and `motwi run`, so Motwi moderates a real Twitch channel headless. Chat arrives over EventSub WebSocket, actions go out through Helix, and tokens are stored in Windows Credential Manager.

**Architecture:**
- The new package `motwi.twitch` is the only code that talks to Twitch.
- `auth` handles the device code flow, token refresh and validation.
- `tokens` persists tokens in keyring.
- `helix` is a rate-limited Helix client.
- `executor` is the `Executor` protocol backed by Helix.
- `eventsub` is the WebSocket session client.
- `convert` turns events into `ChatMessage`.
- `motwi.app` wires the M1 engine to these pieces. `cli` gains `login`, `logout` and `run`.

**Tech Stack:** M1, plus `websockets` (asyncio client) and `keyring`.

**Spec:** `docs/superpowers/specs/2026-10-27-motwi-design.md`, sections 5.1, 5.7 and 5.8, and 7 (logs).

## Global Constraints (in addition to M1's)

- New runtime dependencies are limited to `websockets` and `keyring`. Tests never touch real Twitch or the real keyring.
- The client ID comes from `motwi.twitch.CLIENT_ID`, which stays empty until the owner registers the app. The env var `MOTWI_TWITCH_CLIENT_ID` overrides it. A missing ID gives a clear error.
- Scopes cover the spec's list only:
  - `user:read:chat`, `user:write:chat`
  - `moderator:manage:banned_users`, `moderator:manage:chat_messages`, `moderator:manage:warnings`
  - the bot account also gets `user:read:moderated_channels`, to check that it is a moderator
  - channel info needs no scope
- Refresh tokens rotate: every refresh persists the new pair before anything else uses it. A failed refresh raises `AuthExpired`, which tells the user to run `motwi login`. There is no retry loop.
- Helix: on 401, refresh once and retry. On 429, wait until `Ratelimit-Reset` and retry. Calls are paced by a small concurrency limit.
- The data dir is `%APPDATA%\Motwi`, overridable with `MOTWI_HOME`. Logs go to `<data>\logs\motwi.log`, rotating at 1 MB × 5.

## Decisions

- **Accounts.**
  - The *moderator account* is the bot if one is connected, otherwise the streamer. It reads chat (`channel.chat.message` with `user_id` set to the moderator account) and performs every action.
  - The channel is always the streamer's account.
- **One EventSub connection** with these subscriptions:
  - `channel.chat.message` v1
  - `channel.update` v2
  - `stream.online` v1 and `stream.offline` v1
- **Reconnect.**
  - `session_reconnect` switches to the new URL without re-subscribing.
  - A dropped or silent connection (no frame within keepalive + 5 s) reconnects with backoff (1, 2, 4 … 60 s) and subscribes again.
  - Duplicate `message_id`s are ignored (bounded set of 1000).
- **Shared chat.** Messages whose `source_broadcaster_user_id` names another channel are not moderated, because they are not this community's chatters.
- **Badges.**
  - `set_id`s go into `ChatMessage.badges`.
  - `lead_moderator` counts as `moderator`.
  - `emote_free_text` joins every fragment that isn't an emote.
- **Executor tolerance.** These count as success, since the desired state is already true:
  - "already banned" on ban/timeout
  - "not banned" on unban
  - 404 on delete
- **Stream boundaries.**
  - `stream.offline` resets offenses (spec 5.3).
  - `stream.online` sets `stream_id`.
  - `channel.update` updates title and game.
  - On start, Helix `channels` and `streams` seed the context.
- **Housekeeping in `run`:**
  - `expire_overdue()` every 30 s
  - retention purge at startup and every 6 h
  - every pipeline task gets a done-callback that logs exceptions (addresses M1 open item b)
- **AI for `run`.** Provider and model come from the stored settings. When they're unset, they come from `MOTWI_AI_PROVIDER` / `MOTWI_AI_MODEL`. The key comes from the env var, then keyring (`motwi set-key`).
- **First run.** If the DB has no rules, it is seeded with the starter rules.

## Tasks

1. **Paths, logging, secrets.**
   - `paths.py`: `data_dir()`, `db_path()`, `log_dir()`.
   - `logs.py`: `setup_logging()`.
   - `twitch/tokens.py`: `Token`, `SecretStore` protocol, `KeyringSecrets`, `MemorySecrets`, `TokenStore(load/save/delete per role)`.
   - Tests: round-trip and missing → None.
2. **Device auth and refresh.** `twitch/auth.py`:
   - `start_device_flow`, `poll_device_flow`: pending, slow_down → interval + 5, denied, expired.
   - `refresh` (rotation persisted), `validate`, `TwitchAuth.access_token()`, which refreshes when within 5 minutes of expiry or on demand, behind a lock so concurrent callers refresh once.
   - Tests use respx.
3. **Helix client and executor.**
   - `twitch/helix.py`: `HelixClient`, `HelixError`, the endpoints above.
   - `twitch/executor.py`: `HelixExecutor`, with the tolerance rules and a 500-character reason cap.
   - Tests use respx: 401 → refresh → retry, 429 → wait → retry, and error mapping.
4. **EventSub client and conversion.**
   - `twitch/eventsub.py`: `EventSubClient(connect, subscribe, on_event, sleep)`.
   - `twitch/convert.py`: `chat_message(event, ts)`.
   - Tests use a fake WebSocket: welcome → subscribe, notification delivery, dedup, keepalive timeout → reconnect plus resubscribe, session_reconnect without resubscribe, and revocation logged.
5. **App runner and CLI.**
   - `app.py`: `MotwiApp`, wiring store, live config, scorer, moderation and pipeline to an EventSub source and executor, with stream events and housekeeping.
   - `cli`: `login [--bot]`, `logout [--bot]`, `set-key [--backup]`, `run [--suggest-only]`.
   - Tests: an integration test with fake EventSub events → executor calls, offline resets offenses, and channel.update changes the context.
