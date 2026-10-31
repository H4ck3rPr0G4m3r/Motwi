# Motwi design

Date: 2026-10-27
Status: approved by owner 2026-10-27

Motwi is a rewrite of the older Twitch-Ai-moderator (`../Twitch-Ai-moderator`). It keeps the core idea and redoes the system, the code, and the experience.

The core idea: an LLM scores Twitch chat messages against the streamer's own rules and the surrounding context, and Motwi acts on the score.

Approved mockups live next to this file:

- `mockups/chat-layout-v2.html`: Motwi inside Twitch chat, in three states.
- `mockups/main-window-v2.html`: the main window's Rules and Stats screens.

Decisions marked **(builder's call)** were made by the designer without asking the owner. They are reasonable defaults and can be revisited. Everything else was agreed with the owner.

---

## 1. Intent

The old moderator proved the idea works. It was also slow, unreliable ("doesn't work most times"), painful to set up, and its dashboard was crowded and clunky.

Motwi should feel like a professional app that a streamer's friends can install and trust. The owner (Peter) streams occasionally and will use it then. The plan is to give it to streamer friends first, and later open-source it.

### Who uses it

- **The streamer.** Motwi runs on their PC. During a stream they glance at their second monitor, where Twitch chat is open. They want to see what Motwi decided, what it deleted, and approve or deny ban requests, without opening a separate busy dashboard.
- **The streamer's mods.** If the streamer has mods, the mods can take over ban approvals and reviewing from their own computers, so the streamer's screen stays clear.

### What success looks like

- A friend with no developer knowledge goes from download to "Motwi is watching my chat" in about five minutes. No terminal, no `.env`, no Twitch developer console.
- During a stream, everything the streamer needs happens inside Twitch chat.
- Motwi is honest about its state. When the AI is down, it says so. When a ban is approved, the UI says it happened only after Twitch confirms it.
- AI decisions arrive within a few seconds of the message. Filter decisions are instant.
- It never silently stops working.

## 2. Goals, non-goals, anti-goals

### Goals (v1)

1. AI moderation scored against community rules, per-stream rules, the current game and title, and a short slice of surrounding chat.
2. Every decision shows its score, the matched rule, and a one-line reason.
3. Repeat offenders escalate, tracked in code rather than in the AI prompt.
4. A spam filter plus blocked words. Both act instantly with no AI call, and are conservative.
5. Ban requests that the streamer or a mod approves or denies, from inside Twitch chat.
6. One-click undo on every automatic action, where Twitch allows it (see 5.6).
7. Suggest-only mode: Motwi proposes actions and takes none on its own.
8. Trusted users are never touched. VIPs get leeway. The broadcaster, mods, and Motwi's own bot account are always exempt.
9. Multiple AI providers with an automatic backup. Graceful degradation when the AI is unavailable.
10. A cost and usage counter that persists across restarts.
11. A post-stream recap.
12. A stats page with simple charts.
13. A browser extension that puts Motwi inside Twitch chat.
14. Mods can connect remotely and handle approvals.
15. Moderation as the streamer's own account by default. A separate bot account is optional.
16. A Windows installer with a guided first-run setup.

### Non-goals

- **Teach-the-AI / training feedback.** Rejected by the owner. Do not build it.
- **Polls or any other non-moderation feature.** Motwi is for moderation only.
- **Per-chatter profiles sent to the AI** (account age, follow age, history). Rejected because it bloats the prompt. Account metadata may still be used by the spam filter in code (see 5.4). It is never put in the prompt.
- **Lockdown / raid mode.** Deferred to after v1.
- **Hosting.** Motwi runs on the streamer's PC. There is no Motwi server in the cloud.
- **Mac, Linux, Firefox, Safari, mobile.** v1 targets Windows and Chromium browsers (Chrome, Edge, Brave, Opera). The code should not make a later Mac build hard. Python and web UI keep that open.
- **Migrating data from the old moderator.** Friends are new users. (builder's call)

### Anti-goals: failure even if it technically works

- The spam filter times people out for normal chat behavior. The old filter flagged any 10+ character all-caps message, any repeated emote within 30 seconds, and the phrase "sub to".
- Anything fails silently: dropped messages, failed batches counted as analyzed, a "Bot Online" badge that sticks after a crash.
- The UI claims success before it happens. The old version said "ban approved" before the ban was executed.
- A crowded dashboard you have to watch during a stream.
- Setup that requires creating Twitch developer apps, copying numeric user IDs, or pasting OAuth tokens.
- Settings, rules, or bans lost to file races or corrupt JSON. The old version did this.
- Anyone else on the network, or any web page the streamer visits, can control Motwi. The old dashboard was unauthenticated with CORS `*`.

## 3. Constraints

- Runs on the streamer's Windows PC. The owner was explicit: "staying on my pc definitely."
- Language: Python for the app core. The owner prefers Python, with Rust only "when speed counts". Speed is bound by AI latency, so Python is the agreed choice. The extension must be JavaScript or TypeScript.
- Friends must not need their own Twitch developer app.
- The repo is private now and will be public later. No secrets in the repo, ever. Code and README should be clean enough for strangers.
- Commit regularly to `github.com/H4ck3rPr0G4m3r/Motwi` (private). The owner may stop work between tasks, so each task should leave the repo in a working state.

## 4. Architecture overview

```
 ┌──────────────── Streamer's PC ────────────────────────────────────┐
 │                                                                    │
 │  Motwi app (one Python process, tray icon)                         │
 │   ├─ Twitch link     EventSub WebSocket in, Helix API out          │
 │   ├─ Pipeline        exemptions → blocked words → spam filter      │
 │   │                  → AI scorer → decision engine → executor      │
 │   ├─ Store           SQLite: settings, rules, actions, usage       │
 │   ├─ Local API       HTTP + WebSocket on 127.0.0.1, token auth     │
 │   └─ Window          native window (WebView2) showing the web UI   │
 │                                                                    │
 │  Browser with Twitch open ── Motwi extension ── Local API          │
 └────────────────────────────────────────────────────────────────────┘
          ▲
          │ remote access for mods (see 5.9)
          │
   Mod's browser with Twitch open ── Motwi extension
```

### Technology (builder's call unless noted)

- **Core:** Python 3.12, a single asyncio process. Nothing blocks the event loop: AI calls are async HTTP, and DB access goes through an async wrapper or a worker thread. (The old version blocked the whole bot during every AI call.)
- **Twitch:** talk to EventSub WebSocket and Helix directly with an async HTTP and WebSocket client, not TwitchIO. The surface is small, and the old project suffered from TwitchIO version churn.
- **AI:** one OpenAI-compatible async client covers all providers.
  - Groq, the recommended one. The window offers Groq, OpenRouter and Custom.
  - Gemini through its OpenAI-compatible endpoint. Its card only shows for a slot already using it.
  - OpenRouter.
  - Any custom OpenAI-compatible base URL.
  - This drops LiteLLM, which is huge in a frozen installer. Use JSON/structured output where the provider supports it.
- **Storage:** SQLite in `%APPDATA%\Motwi\`, with schema migrations. This replaces the old JSON-file IPC.
- **Secrets:** Twitch tokens and AI API keys live in the Windows Credential Manager (`keyring`), never in plain files.
- **Local API:** an async HTTP + WebSocket server bound to `127.0.0.1` only. Every client authenticates with a token. CORS allows only the extension's origin and the window.
- **Window:** pywebview (Edge WebView2) showing the UI served by the core. A tray icon (pystray, or whatever works with pywebview) shows status. Closing the window keeps Motwi running in the tray. "Quit" stops it.
- **Front-ends:** TypeScript + Vite for both the window UI and the extension, sharing types and a small component set. Svelte is the suggested framework because it gives small bundles inside Twitch's page, but the builder may choose.
- **Packaging:** PyInstaller (one-folder build) wrapped in an Inno Setup installer. Optional "start with Windows". The installer will be unsigned at first, so SmartScreen will warn; the README must say so.
- **Repo layout:**
  - `core/`: the Python package `motwi`.
  - `ui/`: the window app.
  - `extension/`: the MV3 extension.
  - `packaging/`.
  - `docs/`.

## 5. How it behaves

### 5.1 Message pipeline

For every chat message, in order:

1. **Exemptions.** These users are skipped entirely:
   - the broadcaster
   - the channel's moderators (from the message badges)
   - Motwi's own bot account
   - users on the Trusted list
2. **Blocked words.** A case-insensitive match against the streamer's list. Matching should be smart about obvious obfuscation such as spacing, repeated letters, and common leetspeak, without becoming a regex engine the user must learn.
   - A hit deletes the message instantly and counts as an offense for escalation (5.3).
   - Each blocked word may carry a stronger action, for example "timeout 10m" or "ban request". The default is delete.
3. **Spam filter** (5.4). Instant. Either acts, passes the message on, or marks it "borderline" so the AI gets that user's last few messages as extra context.
4. **AI scorer** (5.2). Async, micro-batched.
5. **Decision engine** (5.3). Turns the score, the rule, and the history into an action.
6. **Executor.** Calls Helix. It records success or failure, and the UI shows the true result.

Every message that reaches the AI stage and cannot be scored is recorded as "unchecked", with the reason. Nothing disappears silently.

### 5.2 AI scoring

- **Micro-batching.** Flush the queue when it reaches N messages or when the oldest message is about 1.5 s old, whichever comes first.
  - Several batches may be in flight at once. Do not wait for one batch to finish before sending the next.
  - The queue has no silent cap. Under extreme load, oldest messages become "unchecked (overload)" and the status bar says so.
- **Target latency:** about 3 s from message to decision under normal load.
- **Prompt contents:**
  1. A fixed system prompt. It covers the moderator role, Twitch culture (gaming violence words, emotes, banter, copypasta), and "lean toward allowing; false positives hurt chat".
  2. The current game/category and stream title, read from Twitch and refreshed on `channel.update`.
  3. Rules, each with a short ID: community rules, then this-stream rules.
  4. A short slice of preceding chat, roughly the last 10-15 lines. It is marked as context only and is not scored.
  5. For spam-borderline users only: that user's last few messages.
  6. The messages to score. Each has an opaque ID and the author's name, and all of them are passed as data (a JSON array), not as free text.
- **Required output** for each message ID:
  - `score`: 0-10
  - `rule`: the ID of the matched rule, or null
  - `reason`: one short line, shown to the streamer
- **The AI does not choose the action.** Code does (5.3).
  - The old version mixed "strict thresholds" and "flexible thresholds" in prompt text, and nothing actually enforced them.
- **Prompt-injection hardening.** Message text is data inside a structure.
  - Results for unknown IDs, or for messages not in the batch, are ignored.
  - Scores are clamped to the 0-10 range.
  - An unparseable response triggers one retry, then goes to the backup provider.
- **Providers.** There is one primary and an optional backup. A call that errors or exceeds a timeout (about 8 s) retries once, then goes to the backup.
  - Repeated failures open a circuit breaker for the primary. While it is open, Motwi uses the backup and probes the primary periodically.
  - If both providers are unavailable, the AI stage is skipped. Blocked words and the spam filter keep working. The status bar shows the orange warning from the mockup.
- **Usage and cost.** Record tokens for every call.
  - Use the cost OpenRouter reports when available.
  - Otherwise, use a small editable price table per model.
  - Store totals permanently, per stream and per day.

### 5.3 Decision engine and escalation

- **Strictness presets** set three thresholds: Relaxed 7 / 8.5 / 9.5, Standard 6 / 7.5 / 9, Strict 5 / 6.5 / 8. Each is the warn / timeout / ban-request threshold.
  - An "advanced" disclosure exposes the raw numbers.
  - Thresholds are always kept in order. The UI explains this instead of silently reordering them.
- **Rule actions.** Each rule carries what it does: `delete`, `warn`, `timeout`, `ban`, `up to timeout`, or `up to ban`.
  - `delete`, `warn`, `timeout` and `ban` are fixed. Any score at or above the warn threshold does that action, on the first offense. `timeout` uses the ladder's lengths and never bans. `ban` is still a ban request for a human to approve.
  - For `up to timeout` and `up to ban`, the score picks the base action and the rule caps it.
  - With no rule matched, the cap is "up to timeout". Bans only ever come from rules that allow them.
  - Any score at or above the warn threshold also deletes the message. (builder's call)
- **Escalation ladder.** The default ladder is warn → timeout 1 min → timeout 10 min → ban request. It is configurable.
  - Each offense in the current stream moves the chatter one step up the ladder. The step never exceeds the rule's ceiling.
  - Offenses reset when the stream ends. If no stream is detected, they reset after 12 hours of inactivity. (builder's call)
  - Undone actions don't count as offenses.
- **VIP leeway.** Twitch VIPs (from the badge) and users on Motwi's VIP list get all thresholds raised by a configurable amount (default +1.0). They are not exempt.
- **Suggest-only mode.** The decision is computed exactly as normal. Instead of executing it, Motwi shows it inline with action buttons, as in the mockup's third state. Blocked words and the spam filter follow the same mode.

### 5.4 Spam filter

- **Principle.** It only acts on its own when it is nearly certain. Anything less becomes "borderline", which sends the message to the AI with that user's recent messages.
- **Cases to handle:**
  - **Floods.** One user posting far faster than a human chats.
  - **Repeats.** One user posting the same or a near-identical message several times in a short window.
  - **Link spam.** Links, including obfuscated ones like `strmboost .shop`, from chatters with no history in this stream, combined with known spam phrasing ("cheap viewers", "followers", "promote your stream").
  - **Character floods and mass mentions.**
- **Things that must never trigger it:** caps alone, emote spam, copypasta shared by many users, and normal enthusiasm.
- **Default action** is delete, then the escalation ladder. Thresholds are configurable, with sensible defaults.
- **Test it on realistic chat before shipping.** Build a small fixture corpus that includes normal hype, emote walls, and copypasta. False positives on that corpus are bugs.
- **Account metadata.** The filter may use cheap metadata in code, such as first message in this channel, never in the prompt.

### 5.5 Ban requests

- When the decision is "ban request", Motwi:
  1. deletes the message
  2. immediately puts the chatter in a **hold timeout**, so they can't keep posting while someone decides. This is a setting: on by default, with a configurable length (default 10 min). With it off, the chatter stays in chat until someone answers
  3. shows the pop-up to the streamer and every connected mod
- The pop-up shows the score, the message, the reason, Ban / Deny buttons, and who is currently looking at it.
- **The first answer wins.** Everyone's pop-up then collapses to a one-line receipt ("xx_v0id_xx banned by Brakkenhoff", with Undo), which fades after a few seconds.
- **Deny** lifts the hold timeout and records the request as denied.
- **If nobody answers before the hold ends,** the request stays open as "expired". Motwi does not auto-ban. It reminds the streamer in the bar.
- **Results are confirmed.** A ban shows as done only after Helix confirms it. A failure shows an error with Retry.

### 5.6 Undo

| Action | Undo does | Twitch effect |
|---|---|---|
| Timeout | removes the timeout | Helix unban |
| Ban | unbans | Helix unban |
| Delete | marks it as a Motwi mistake | none. Twitch cannot restore deleted messages |
| Warn | marks it as a Motwi mistake | none. Twitch warnings cannot be retracted |

- Undone actions show as "Undone" inline, are excluded from escalation, and count as "undone" in stats.
- **Label correction.** The mockup said "Restored" for an undone delete. The UI must instead make clear that a delete can't be brought back on Twitch. "Marked as mistake" or similar.

### 5.7 Warnings

- The default is Twitch's native warning (Helix warn chat user), which the chatter must acknowledge.
- A setting switches to an `@user` reply in chat instead.

### 5.8 Twitch connection and accounts

- **Auth.** Use the Device Code Grant Flow with Motwi's own public Twitch app. Peter registers the app as a public client, and its client ID ships in the code; this is fine because it is not a secret. The UI shows the code and opens `twitch.tv/activate`. No client secret exists.
- **Accounts.** By default the streamer authorizes their own account, which is used both to read chat and to moderate.
  - Optionally, a second "bot" account can be connected the same way. That account must be a moderator in the channel; Motwi checks this and tells the user how to fix it.
- **Scopes.** Request only what is needed:
  - reading chat
  - sending chat
  - managing banned users (ban, timeout, unban)
  - deleting chat messages
  - warnings
  - reading channel info
  - reading the channel's moderators (streamer's account only, for mod access)
- **Tokens** refresh automatically. Twitch rotates refresh tokens for public clients, so each new one must be persisted. If a refresh fails, the UI shows "Reconnect Twitch" and does not loop on errors.
- **EventSub WebSocket** subscriptions: chat messages, `channel.update` for title and game, and `stream.online` / `stream.offline` for stream boundaries and recaps.
  - Add moderation events if useful, for example to notice that a human mod already banned someone.
  - Reconnect with backoff, and handle Twitch's reconnect messages.
- **Rate limits.** Respect Helix rate limits with a small queue in the executor.

### 5.9 Mods' remote access

Mod access is the **last part of v1 to build**. Mods can always moderate through Twitch itself, so Motwi is fully useful without it.

- **Inviting and verifying a mod.**
  - The streamer clicks "Invite a mod" on the Mods page, which creates a one-time invite link.
  - The mod installs the extension, opens the link, and signs in with Twitch in the extension.
  - Motwi checks that this Twitch user really is a moderator of the channel, and re-checks periodically. A leaked link alone is useless.
  - The paired extension gets its own revocable token. (builder's call, settled after review)
  - The Twitch sign-in asks for no scopes, runs in the extension's own page, and is revoked as soon as Motwi has read who it belongs to. Nothing keeps it. Mod status is checked with the streamer's token.
- **What mods can do:** approve or deny ban requests, undo actions, and act on suggestions. Mods cannot change rules or settings in v1. (builder's call)
- **Attribution.** Every action records which mod took it, and the reason sent to Twitch names them.
- **Transport (owner's decision: option A now, B possible later).**
  - Motwi bundles `cloudflared` and runs a "quick tunnel". It needs no account, needs no open ports, and runs only while at least one mod is paired and Motwi is running.
  - All traffic through it is authenticated with per-mod tokens.
  - The tunnel reaches a separate local server with only the mods' routes, never the local API.
  - Anyone can get a `trycloudflare.com` address, so before sending a token to an address, the extension asks it to prove it holds that token's hash, bound to the address.
  - **The tunnel address changes every time Motwi restarts.** In v1:
    - Mods' extensions detect that the old address is gone and show "Motwi moved, ask the streamer for the new link".
    - Motwi shows the streamer a fresh link, one click to copy, to send to mods.
    - Re-pairing with a fresh link must not require the mod to sign in with Twitch again.
  - **Later option B, not in v1.** A tiny optional "phonebook" (for example a free Cloudflare Worker) that stores only each streamer's current encrypted address, so mods reconnect automatically. Keep the address lookup behind one small interface so B can be added without reworking pairing.

### 5.10 Post-stream recap

- Generated in code on `stream.offline`, with no AI involved.
- Contents:
  - number of messages checked
  - counts per action, with undone and denied counts
  - top triggering rules
  - chatters who escalated the furthest
  - AI cost
  - any AI outages
- Shown on Overview and as a Windows notification. This-stream rules are cleared at the same time.

## 6. The experience

### 6.1 Inside Twitch chat (extension)

Approved design: `mockups/chat-layout-v2.html`. It combines inline marks in chat lines with the status bar and the ban pop-up.

- **Status bar** above chat:
  - the Motwi mark
  - state (Watching / Suggesting / Paused / AI down)
  - the active model
  - live counts
  - the on/off switch
  - Clicking the counts drops down a short log of recent actions. This catches actions whose chat lines have scrolled away.
- **Inline marks on the actual chat lines.** Motwi's acted-on messages stay visible to the streamer and mods, struck through, with:
  - an action tag and score
  - the reason
  - Undo
  - an escalation note such as "2nd offense this stream"
- **Only extension users see them.** Viewers see Twitch's normal behavior.
- **Ban pop-up** slides in over the top of chat. Its behavior is described in 5.5.
- **Suggest-only mode** shows suggestions highlighted inline, with action buttons.
- **Where it runs:**
  - the channel page
  - pop-out chat
  - Twitch's moderator view
  - Stream Manager chat
  - It activates only on the channel it is paired with.
- **Theme.** Twitch has light and dark themes, and the extension must look right in both.
- **Matching chat lines.** Matching a Twitch DOM chat line to a message ID is the main technical risk.
  - The builder should investigate approaches: React props as used by 7TV/FFZ-style tools, or a robust match on user + text + time.
  - Lines that can't be matched still appear in the bar's log, so nothing is lost.
- **Extension architecture.** Manifest V3. The extension connects to the local API from its background or offscreen context, because content scripts can't reliably reach `127.0.0.1`.
  - **Pairing with the streamer's own Motwi:** one click from the main window. There is no code typing, but there must be a real token.

### 6.2 Main window

Approved design: `mockups/main-window-v2.html`.

- **Layout:** a left rail with Overview, Rules, History, People, Stats, Mods, and Settings. A status block sits at the bottom of the rail: channel and live state, the AI and its backup, and this month's cost.
- **Overview:**
  - current state and stream
  - today's numbers
  - open ban requests
  - the last recap
  - any warnings (Twitch disconnected, AI down, update available)
- **Rules:**
  - community rules, each with a ceiling chip
  - this-stream rules, with the game and title read from Twitch
  - blocked words, with slurs collapsed behind a count
  - the strictness control
  - "Try a message", which scores text with the real rules and real model and shows what Motwi would do
- **History:**
  - every action, searchable
  - filters by action, rule, chatter, stream, and who acted (Motwi, the streamer, a mod)
  - undo from here as well
  - clicking a chatter shows their Motwi history
- **People:**
  - Trusted list
  - VIP list and the leeway setting
  - recently actioned chatters
  - currently banned or timed-out chatters, with unban
- **Stats:**
  - range tabs: last stream, 7 days, 30 days
  - totals per action, with undone and denied counts
  - actions per day as a stacked bar chart
  - "what triggered them" ranking
  - AI cost
  - Bars need hover tooltips and an accessible table view.
- **Mods:** invite, list, revoke, and see who is connected right now.
- **Settings:**
  - Twitch accounts: connect, reconnect, bot account
  - AI providers: primary and backup, keys, model, Test button
  - action behavior: warn style, ladder, hold timeout length, delete-on-warn
  - suggest-only
  - data retention
  - start with Windows
  - updates
  - Settings save with clear per-field feedback. Use one save model everywhere; the old one mixed autosave and explicit saves.
- **Every view** has real empty, loading, and error states.

### 6.3 First-run setup

This is a guided flow in the main window.

1. Welcome.
2. Connect Twitch with the device code. Optionally add a bot account.
3. Pick an AI provider, paste the key, and press Test. Optionally add a backup.
4. Pick strictness, and start from an editable starter set of community rules. Offer suggest-only as a starting mode.
5. Install the extension and connect it. Link to the store listing, or to manual-install steps until the listing exists.
6. Done. Motwi starts watching and shows the status.

Each step validates for real before moving on. Every step can be revisited later in Settings.

### 6.4 Visual language

Carry the mockups' language through both front-ends.

- **Theme.** Twitch-native dark, with surfaces around `#131316` / `#18181b` and lines around `#2a2a30` / `#2f2f35`. The single Motwi accent is teal, `#4fd1b5`.
- **Fonts.** Inter for UI text. JetBrains Mono for numbers and scores. Self-host both; no CDN fonts.
- **Action colors.** One mapping used everywhere: chat tags, charts, and chips.
  - Deleted: `#3987e5`
  - Warned: `#c98500`
  - Timeout: `#d95926`
  - Ban: `#d55181`
  - This set passed a color-vision-deficiency check on dark surfaces in stack order warn / ban / deleted / timeout.
  - In chat, always show a text label next to the color.
  - The first mockup's tag colors were different. Use this set instead.
- **Icons.** Phosphor icons.
- **Radius.** 6 px for panels and inputs, 4 px for chips and buttons.
- **Light theme.** Required in the extension because Twitch has one. Optional in the window for v1.
- **Accessibility.**
  - Real labels on inputs, never placeholder-only.
  - Accessible names on icon buttons.
  - Visible focus rings.
  - AA contrast.
  - `aria-live` for the ban pop-up and status changes.
  - Reduced-motion support.

## 7. Privacy and data

- Store only messages that Motwi acted on, along with the context lines shown with them. Do not log all chat.
- Default retention is 30 days, and it is configurable.
- Full AI request/response logging is off by default. It is a debug toggle with rotation.
- Logs are rotated and size-capped, and live in `%APPDATA%\Motwi\logs`.

## 8. Quality bar

- **Unit tests** for:
  - the decision engine (thresholds, ceilings, ladder, VIP leeway, suggest-only)
  - the spam filter, including the false-positive corpus
  - blocked-word matching
  - prompt building and response parsing, including malformed and hostile responses
- **Pipeline tests** with a fake Twitch and a fake LLM.
- **A dev simulator.** A command that feeds scripted or recorded chat into the pipeline with Twitch actions stubbed out. It is used for UI and extension development and for demos. It replaces the old "demo mode" in a dev-only form.
- **Manual verification** of the extension on live Twitch pages before calling UI work done.

## 9. Distribution

- **While the repo is private,** friends can't download GitHub releases. Installers are shared directly.
- **After open-sourcing,** move to GitHub Releases and add an in-app update check. The update check should already be built against a configurable URL.
- **Extension.** An unlisted Chrome Web Store listing. The one-time developer fee and the listing are Peter's to set up. Until it exists, ship a zip with manual-install instructions.
- **License.** Decided at open-source time. Not needed for v1.

## 10. Owner to-dos

- **Register the public Twitch app "Motwi"** on dev.twitch.tv: client type Public, with Device Code flow enabled. Until that exists, the client ID is configurable (dev setting / environment variable) so development can proceed. Development can use a mocked Twitch through the simulator.
- **Chrome Web Store unlisted listing**, when ready to give the extension to friends.

## 11. Left to the builder

- The exact batch sizes, timeouts, spam thresholds, and starter rules. Make them sensible and tunable.
- The front-end framework, if Svelte turns out to be the wrong fit.
- The approach for matching chat lines (6.1).
- The local API's shape.
- The SQLite schema.
- The order in which to build things, as long as each task leaves the repo working and committed.
