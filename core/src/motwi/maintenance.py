"""Jobs the installer runs on the app: a self-test of the frozen build, and a clean wipe."""

import asyncio
import json
import logging
import shutil
import ssl
from collections.abc import Callable
from pathlib import Path

from motwi import __version__
from motwi.twitch.tokens import SecretStore, TokenStore, ai_key_name

log = logging.getLogger(__name__)

DATA_FILES = ("motwi.db", "motwi.db-wal", "motwi.db-shm", "motwi.ico")
DATA_DIRS = ("logs", "webview")
PROBE_SERVICE = "Motwi self-test"


def wipe(secrets: SecretStore, home: Path) -> None:
    """Forget sign-ins and keys, and delete Motwi's own files. Anything else in the folder
    stays, and so does the folder then."""
    tokens = TokenStore(secrets)
    tokens.delete("streamer")
    tokens.delete("bot")
    secrets.delete(ai_key_name("primary"))
    secrets.delete(ai_key_name("backup"))
    for name in DATA_FILES:
        (home / name).unlink(missing_ok=True)
    for name in DATA_DIRS:
        shutil.rmtree(home / name, ignore_errors=True)
    try:
        home.rmdir()
    except OSError:
        log.info("left %s in place: it has other files in it", home)


def _check_files() -> str:
    from motwi.paths import extension_dir, ui_dir

    ui = ui_dir()
    for needed in (ui / "index.html", ui / "pair.html", ui / "pair.js"):
        if not needed.is_file():
            raise FileNotFoundError(needed)
    manifest = json.loads((extension_dir() / "manifest.json").read_text(encoding="utf-8"))
    if manifest["version"] != __version__:
        raise ValueError(f"extension is {manifest['version']}, app is {__version__}")
    return f"ui and extension {manifest['version']}"


def _check_keyring() -> str:
    # a frozen build that loses keyring's entry points falls back to a backend that can't
    # store anything, and every sign-in would then vanish
    import keyring
    from keyring.errors import PasswordDeleteError

    backend = keyring.get_keyring()
    name = type(backend).__name__
    if "Win" not in name:
        raise RuntimeError(f"wrong keyring backend: {name}")
    keyring.set_password(PROBE_SERVICE, "probe", "ok")
    try:
        if keyring.get_password(PROBE_SERVICE, "probe") != "ok":
            raise RuntimeError("keyring didn't return what was stored")
    finally:
        try:
            keyring.delete_password(PROBE_SERVICE, "probe")
        except PasswordDeleteError:
            pass
    return name


def _check_window() -> str:
    import webview  # noqa: F401
    from webview.platforms import winforms  # noqa: F401  (loads .NET and the WebView2 bindings)

    return "winforms"


def _check_tray() -> str:
    import pystray._win32  # noqa: F401

    from motwi.desktop import icon_image

    return f"icon {icon_image('watching', 32).size[0]}px"


def _check_tls() -> str:
    import httpx

    context = httpx.create_ssl_context()
    if not isinstance(context, ssl.SSLContext) or not context.get_ca_certs():
        raise RuntimeError("no CA certificates, so Twitch and AI calls would all fail")
    return f"{len(context.get_ca_certs())} CA certificates"


def _check_store() -> str:
    from motwi.store import MIGRATIONS, Store

    async def run() -> int:
        store = await Store.open(":memory:")
        try:
            return len(await store.list_rules())
        finally:
            await store.close()

    asyncio.run(run())
    return f"schema v{len(MIGRATIONS)}"


def _check_web() -> str:
    import aiohttp
    import websockets

    return f"aiohttp {aiohttp.__version__}, websockets {websockets.__version__}"


CHECKS: dict[str, Callable[[], str]] = {
    "files": _check_files,
    "keyring": _check_keyring,
    "window": _check_window,
    "tray": _check_tray,
    "tls": _check_tls,
    "store": _check_store,
    "web": _check_web,
}


def self_test(report: Path) -> bool:
    """Write a JSON report of what this build can do, for the build to read."""
    results: dict[str, dict] = {}
    for name, check in CHECKS.items():
        try:
            results[name] = {"ok": True, "detail": check()}
        except Exception as e:  # noqa: BLE001  (any failure is the report)
            results[name] = {"ok": False, "detail": f"{type(e).__name__}: {e}"}
    ok = all(r["ok"] for r in results.values())
    report.write_text(
        json.dumps({"ok": ok, "version": __version__, "checks": results}, indent=2),
        encoding="utf-8",
    )
    return ok
