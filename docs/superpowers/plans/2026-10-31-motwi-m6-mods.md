# M6: mod access

Spec: `docs/superpowers/specs/2026-10-27-motwi-design.md`, section 5.9, plus the pop-up in 5.5.

**Goal:** the streamer invites a mod with a link. The mod signs in with Twitch in the extension, Motwi checks they really mod the channel, and from then on they can answer ban requests, undo actions and act on suggestions from their own computer, with every action credited to them.

## Decisions

- **A separate gateway, not the local API.** cloudflared points at a second aiohttp app on its own localhost port. It has only the mod routes, so `/api/quit`, settings and the window's files can't be reached from the internet by any mistake in a route check. It runs only while it's needed.
- **Mod tokens and browser tokens don't overlap.** A mod token is refused by the local API, and a streamer's browser token is refused by the gateway.
- **Twitch sign-in proves identity and nothing else.** The extension runs the device flow itself with no scopes, so a fake invite can't ask for more. The token goes to Motwi once, which reads who it belongs to, and the extension then revokes it. Nothing keeps it.
- **Mod checks use the streamer's token.** `GET /moderation/moderators` needs `moderation:read`, a new scope, so an existing sign-in has to reconnect once before inviting. Motwi checks at join, whenever a mod connects, and every 10 minutes. Only a definite "not a mod" removes someone. A Twitch outage doesn't.
- **Motwi proves itself before a token is sent.** Anyone can get a `trycloudflare.com` address, so a "new link" could be a trap. Before sending its token anywhere, the extension asks for `HMAC(sha256(token), origin|nonce)`. Only the real Motwi knows that hash, and binding the origin defeats a relay.
- **Two kinds of link.** An invite has a one-time code in the fragment and needs a Twitch sign-in. The address link has no secret and only moves mods who are already paired, so it can be pasted in a group chat. A leaked one is useless.
- **The browser can hold several connections.** One's own Motwi plus any number of channels it mods for, so joining a friend's channel doesn't disconnect your own.
- **Address lookup is one function** on each side (`currentOrigin` in the extension, the tunnel's `on_change` in the core), so the optional phonebook can be added later without touching pairing.
- **The gateway serves the extension as a zip,** since it isn't in the Chrome Web Store yet and a mod has no other way to get it.
- **cloudflared is pinned by version and SHA-256** in `packaging/build.py`, and tied to Motwi with a Windows job object so it can't outlive the app.

## Tasks

1. Core: scope and `is_moderator`, `clients` columns, `Tunnel`, `ModAccess`, gateway, window routes, shared API helpers.
2. Extension: several connections in the worker, the mod role, the "moved" state, the join page and bridge, who's looking in the pop-up.
3. Window: the Mods page, and the "send the new link" notice on Overview.
4. Packaging: bundle cloudflared, its license, self-test, install test. Docs.
5. Verify: core tests, extension e2e with two browsers against the demo, one real tunnel run on demo data.

## Failure modes to pin with tests

- A non-mod with a valid invite is refused, and the invite still works for the real mod.
- A wrong `client_id` on the Twitch token is refused.
- A mod demoted on Twitch loses access; a Twitch outage during the check doesn't remove anyone.
- A mod token can't reach settings, pause, or the local API at all.
- The extension never sends its token to an address that fails the proof.
- The tunnel stops when the last mod and invite are gone, and restarts with a new address after a crash.
