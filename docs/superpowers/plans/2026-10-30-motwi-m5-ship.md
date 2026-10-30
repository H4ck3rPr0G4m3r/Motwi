# Motwi M5: ship it

**Goal:** a friend downloads one installer and has a working Motwi, with a recap after each stream, optional start with Windows, and an update check.

**Spec:** `docs/superpowers/specs/2026-10-27-motwi-design.md` (5.10, 6.2, 6.3, 9).

**Stops at:** building and testing the installer. Publishing it and the Chrome Web Store listing are the owner's. Mods' remote access is M6.

## Design

**Recap (spec 5.10)**

- `motwi/recap.py` builds it in code, no AI: messages checked, counts per action with undone and denied, top triggers, chatters who escalated furthest, AI cost, AI outages.
- The recap is a saved snapshot on `streams.recap`, since this-stream rules are cleared at the same moment and rule text would otherwise be lost. Retention removes old recaps, because they name chatters.
- `ai_outages` records AI down and backup spans. A recap reads them by time, so an outage across a stream boundary still counts.
- When Motwi wasn't running as a stream ended, the next session finishes that stream from its last activity and the recap says so. No notification for those.
- Shown on Overview and as a Windows notification.

**Start with Windows**

- The registry's Run value is the only source of truth, so Settings and the installer's checkbox can't disagree.
- Only available in the installed app, since a dev checkout has no stable command to register.
- `Motwi.exe --background` starts in the tray with no window.

**Update check (spec 9)**

- Looks and links, never downloads. `https://` links only.
- Default URL is the GitHub Releases API, overridable by `MOTWI_UPDATE_URL`; empty turns it off. Accepts GitHub's shape and a plain `{version, url, notes}`.
- A 404 means nothing is published yet, which is the honest state while the repo is private.
- A failed check shows in Settings only, never as a warning on Overview.

**Installer**

- PyInstaller one-folder, wrapped by Inno Setup. Per-user install: no admin prompt.
- `Motwi.exe --self-test` proves the frozen app has what it needs: UI files, extension, Credential Manager round trip, WebView2 bindings, tray, TLS, SQLite. Missing packaged pieces fail at build time, not on a friend's PC.
- `--quit` asks a running Motwi to stop, so upgrades and uninstalls finish in-flight messages. Guarded like `/api/show`: a web page can't send the header.
- `--wipe` deletes Motwi's own files and credentials. Uninstall offers it, default no.
- `packaging/build.py` makes `Motwi-Setup-<version>.exe` and the extension zip. `packaging/test_install.py` installs silently into a temp folder, launches, quits and uninstalls.

## Review focus

1. A stream that ends with Motwi closed must still get a recap, and must not be finished twice.
2. A recap must not count undone or denied actions as standing, and an empty stream must still produce one.
3. An update response with a bad version or a non-https link must offer nothing.
4. The frozen app must find its keyring backend. A silent fallback would lose sign-ins.
5. Uninstall must remove the Run value and leave user data unless asked.

## Verification

`uv run pytest`, `npm run check`, `python packaging/build.py`, `python packaging/test_install.py`.
