"""The desktop app: core on an asyncio thread, a WebView2 window, and a tray icon.

Closing the window hides it; moderation keeps running in the tray. Quit stops everything,
letting in-flight messages finish first.
"""

import asyncio
import logging
import os
import sys
import threading
from collections.abc import Callable
from concurrent.futures import Future
from typing import Any

import httpx

from motwi.api import DEFAULT_PORT, ApiAuth, create_app, serve
from motwi.api.server import PortInUse
from motwi.events import AIStateChanged, StatusChanged
from motwi.logs import setup_logging
from motwi.paths import data_dir, db_path, log_dir, ui_dir
from motwi.service import Motwi
from motwi.store import Store

log = logging.getLogger(__name__)

TEAL = (79, 209, 181, 255)
INK = (6, 35, 29, 255)
DOTS = {
    "watching": None,
    "suggesting": (57, 135, 229, 255),
    "paused": (201, 133, 0, 255),
    "connecting": (201, 133, 0, 255),
    "stopped": (138, 138, 148, 255),
    "reconnect": (229, 72, 77, 255),
}
LABELS = {
    "watching": "Watching",
    "suggesting": "Suggesting",
    "paused": "Paused",
    "connecting": "Connecting to Twitch",
    "stopped": "Not watching",
    "reconnect": "Reconnect Twitch",
}


def port() -> int:
    return int(os.environ.get("MOTWI_PORT") or DEFAULT_PORT)


def _message_box(text: str) -> None:
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, "Motwi", 0x10)
    else:
        print(text, file=sys.stderr)


def focus_running(p: int) -> bool:
    """If Motwi already runs on this port, bring its window forward and return True."""
    try:
        ping = httpx.get(f"http://127.0.0.1:{p}/api/ping", timeout=1.5)
        if ping.status_code != 200 or ping.json().get("app") != "motwi":
            return False
        httpx.post(f"http://127.0.0.1:{p}/api/show", headers={"X-Motwi": "1"}, timeout=1.5)
        return True
    except (httpx.HTTPError, ValueError):
        return False


def icon_image(state: str = "watching", size: int = 64):
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, size - 3, size - 3), radius=size // 4, fill=TEAL)
    try:
        font: Any = ImageFont.truetype("segoeuib.ttf", int(size * 0.58))
    except OSError:
        font = ImageFont.load_default(size=int(size * 0.58))
    d.text((size / 2, size / 2 + 1), "M", font=font, fill=INK, anchor="mm")
    dot = DOTS.get(state)
    if dot is not None:
        r = size // 5
        x0 = size - 2 * r - 1
        d.ellipse((x0 - 3, x0 - 3, size + 2, size + 2), fill=(15, 15, 18, 255))
        d.ellipse((x0, x0, size - 1, size - 1), fill=dot)
    return img


class Core:
    """The service, the API and the Twitch session, on their own event loop thread."""

    def __init__(self, p: int, on_show: Callable[[], None]) -> None:
        self.port = p
        self.auth = ApiAuth()
        self._on_show = on_show
        self.loop = asyncio.new_event_loop()
        self.motwi: Motwi | None = None
        self._ready: Future[None] = Future()
        self._stop: asyncio.Event | None = None
        self._thread = threading.Thread(target=self._run, name="motwi-core", daemon=True)
        self.on_status: Callable[[str, bool], None] = lambda state, paused: None

    def start(self, timeout: float = 20) -> None:
        self._thread.start()
        self._ready.result(timeout)

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._main())
        except BaseException as e:  # reported through _ready if startup failed
            if not self._ready.done():
                self._ready.set_exception(e)
            else:
                log.exception("core stopped unexpectedly")
        finally:
            self.loop.close()

    async def _main(self) -> None:
        self._stop = asyncio.Event()
        store = await Store.open(db_path())
        runner = None
        try:
            self.motwi = motwi = await Motwi.open(store, secrets=_secrets(), env=os.environ)
            app = create_app(
                motwi,
                auth=self.auth,
                port=self.port,
                ui_dir=ui_dir(),
                on_show=self._on_show,
            )
            runner = await serve(app, self.port)
            watcher = asyncio.create_task(self._watch_status(motwi))
            await motwi.connect()
            self._ready.set_result(None)
            await self._stop.wait()
            watcher.cancel()
        finally:
            if runner is not None:
                await runner.cleanup()
            if self.motwi is not None:
                await self.motwi.aclose()
            await store.close()

    async def _watch_status(self, motwi: Motwi) -> None:
        q = motwi.bus.subscribe()
        last = None
        try:
            while True:
                current = (motwi.engine_state(), motwi.live.settings.paused)
                if current != last:
                    last = current
                    try:
                        self.on_status(*current)
                    except Exception:
                        log.exception("tray update failed")
                while not isinstance(await q.get(), (StatusChanged, AIStateChanged)):
                    pass
        finally:
            motwi.bus.unsubscribe(q)

    def call(self, coro) -> None:
        asyncio.run_coroutine_threadsafe(coro, self.loop)

    def stop(self, timeout: float = 15) -> None:
        if self._stop is not None and self.loop.is_running():
            self.loop.call_soon_threadsafe(self._stop.set)
        self._thread.join(timeout)


def _secrets():
    from motwi.twitch.tokens import KeyringSecrets

    return KeyringSecrets()


class Tray:
    def __init__(self, *, on_open, on_pause, on_quit) -> None:
        import pystray

        self._paused = False
        self._state = "stopped"
        self._on_pause = on_pause
        menu = pystray.Menu(
            pystray.MenuItem("Open Motwi", lambda: on_open(), default=True),
            pystray.MenuItem(
                lambda _: "Resume moderation" if self._paused else "Pause moderation",
                lambda: self._on_pause(not self._paused),
                enabled=lambda _: self._state not in ("stopped", "reconnect"),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit Motwi", lambda: on_quit()),
        )
        self.icon = pystray.Icon("Motwi", icon_image("stopped"), "Motwi", menu)

    def start(self) -> None:
        self.icon.run_detached()

    def update(self, state: str, paused: bool) -> None:
        self._state, self._paused = state, paused
        self.icon.icon = icon_image(state)
        self.icon.title = f"Motwi: {LABELS.get(state, state)}"
        self.icon.update_menu()

    def notify(self, text: str) -> None:
        try:
            self.icon.notify(text, "Motwi")
        except Exception:
            log.debug("tray notification failed", exc_info=True)

    def stop(self) -> None:
        self.icon.stop()


def run_app() -> int:
    setup_logging(log_dir())
    p = port()
    if focus_running(p):
        return 0

    import webview

    window_ref: dict[str, Any] = {}
    quitting = threading.Event()
    hinted = threading.Event()

    def show() -> None:
        w = window_ref.get("window")
        if w is not None:
            w.show()
            w.restore()

    core = Core(p, on_show=show)
    try:
        core.start()
    except PortInUse:
        _message_box(
            f"Motwi can't start: another program is using port {p}.\n"
            "Close it, or set MOTWI_PORT to a free port."
        )
        return 1
    except Exception as e:
        log.exception("Motwi failed to start")
        _message_box(f"Motwi can't start: {e}\n\nDetails are in {log_dir()}.")
        return 1

    def quit_app() -> None:
        quitting.set()
        core.stop()
        tray.stop()
        w = window_ref.get("window")
        if w is not None:
            w.destroy()

    def pause(paused: bool) -> None:
        if core.motwi is not None:
            core.call(core.motwi.set_paused(paused))

    tray = Tray(on_open=show, on_pause=pause, on_quit=quit_app)
    core.on_status = tray.update
    if core.motwi is not None:
        tray.update(core.motwi.engine_state(), core.motwi.live.settings.paused)
    tray.start()

    window = webview.create_window(
        "Motwi",
        url=f"http://127.0.0.1:{p}/#k={core.auth.window_token}",
        width=1240,
        height=800,
        min_size=(960, 640),
        background_color="#131316",
        text_select=True,
    )
    window_ref["window"] = window

    def on_closing() -> bool:
        if quitting.is_set():
            return True
        window.hide()
        if not hinted.is_set():
            hinted.set()
            tray.notify("Motwi is still watching chat. Quit from the tray icon.")
        return False

    window.events.closing += on_closing
    webview.start(
        private_mode=True,
        storage_path=str(data_dir() / "webview"),
        icon=None,
    )
    if not quitting.is_set():  # the window went away some other way
        quitting.set()
        core.stop()
        tray.stop()
    return 0


def main() -> None:
    sys.exit(run_app())
