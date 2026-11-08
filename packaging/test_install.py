"""Installs the built installer for real, in a throwaway folder and data home, then uninstalls.

    python packaging/test_install.py [path-to-installer]

Checks the whole round trip a friend gets: silent install, the Run value when asked for, the
frozen app's self-test from its installed location, a real launch whose page runs in WebView2,
starting hidden with --background, a graceful --quit, --wipe, and an uninstall that removes the
files and the Run value but not the user's data.

It refuses to run if Motwi is already installed or set to autostart here, and it uses its own
port, data folder and Credential Manager namespace, so it can't disturb a real install or see
real sign-ins.
"""

import ctypes
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import winreg
from ctypes import wintypes
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
UNINSTALL_KEY = (
    r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    r"\{7E2C7B1E-5A0F-4C57-9C3B-4D4B8E6F2A11}_is1"
)
PORT = 47961
QUIT_LIMIT_S = 10


def reg_value(key: str, name: str | None) -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            return str(winreg.QueryValueEx(k, name)[0])
    except FileNotFoundError:
        return None


def http(path: str, *, method: str = "GET", headers: dict | None = None) -> tuple[int, bytes]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}", method=method, headers=headers or {}
    )
    with urllib.request.urlopen(request, timeout=3) as resp:
        return resp.status, resp.read()


def wait_for(what: str, check, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if check():
                return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.5)
    raise SystemExit(f"FAIL: timed out waiting for {what}")


def visible_window(pid: int) -> bool:
    """Whether this process has a visible top-level window titled Motwi."""
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            title = ctypes.create_unicode_buffer(64)
            user32.GetWindowTextW(hwnd, title, 64)
            if title.value == "Motwi":
                found.append(hwnd)
        return True

    user32.EnumWindows(visit, 0)
    return bool(found)


def log_text(home: Path) -> str:
    path = home / "logs" / "motwi.log"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def ok(what: str, condition: bool, detail: object = "") -> None:
    print(f"{'PASS' if condition else 'FAIL'} {what} {detail}", flush=True)
    if not condition:
        raise SystemExit(1)


def main() -> None:
    if len(sys.argv) > 1:
        installer = Path(sys.argv[1])
    else:
        installer = max((HERE / "out").glob("Motwi-Setup-*.exe"), key=lambda p: p.stat().st_mtime)
    if reg_value(UNINSTALL_KEY, "DisplayName") or reg_value(RUN_KEY, "Motwi"):
        raise SystemExit("Motwi is installed or set to autostart on this PC. Not touching it.")

    tmp = Path(tempfile.mkdtemp(prefix="motwi-install-"))
    app_dir = tmp / "app dir with spaces"
    home = tmp / "home"
    env = {
        **os.environ,
        "MOTWI_HOME": str(home),
        "MOTWI_PORT": str(PORT),
        "MOTWI_KEYRING_SERVICE": f"Motwi install test {os.getpid()}",
        "MOTWI_UPDATE_URL": "",
    }
    exe = app_dir / "Motwi.exe"
    proc = None
    try:
        print(f"Installing {installer.name}", flush=True)
        subprocess.run(
            [
                str(installer),
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
                "/CURRENTUSER",
                f"/DIR={app_dir}",
                "/TASKS=autostart",
            ],
            check=True,
            timeout=300,
        )
        ok("files installed", exe.is_file())
        ok("uninstaller registered", reg_value(UNINSTALL_KEY, "DisplayName") == "Motwi")
        run_value = reg_value(RUN_KEY, "Motwi")
        ok("start with Windows registered", run_value == f'"{exe}" --background', run_value)
        start_menu = Path(os.environ["APPDATA"]) / r"Microsoft\Windows\Start Menu\Programs"
        ok(
            "cloudflared is bundled with its license",
            (app_dir / "_internal" / "cloudflared" / "cloudflared.exe").is_file()
            and (app_dir / "_internal" / "cloudflared" / "LICENSE.txt").is_file(),
        )
        ok("Start menu shortcut", (start_menu / "Motwi.lnk").is_file())

        report = tmp / "report.json"
        code = subprocess.run(
            [str(exe), "--self-test", str(report)], env=env, timeout=120, check=False
        ).returncode
        data = json.loads(report.read_text(encoding="utf-8"))
        bad = {k: v["detail"] for k, v in data["checks"].items() if not v["ok"]}
        ok("self-test from the installed folder", code == 0 and not bad, bad or "")

        print("Launching", flush=True)
        proc = subprocess.Popen([str(exe)], env=env)
        wait_for("the API", lambda: http("/api/ping")[0] == 200)
        status, body = http("/")
        ok("serves the window's files", status == 200 and b"<html" in body.lower())
        status, _ = http("/pair.html")
        ok("serves the pairing page", status == 200)
        version = json.loads(http("/api/ping")[1])["version"]
        ok("reports its version", version == data["version"], version)
        ok("the database was created in the data home", (home / "motwi.db").is_file())
        wait_for(
            "the window's page to run in WebView2 and connect",
            lambda: "event socket opened (window)" in log_text(home),
            timeout=45,
        )
        ok("the window's page runs and connects", True)
        ok("first run shows the window", visible_window(proc.pid))

        second = subprocess.run([str(exe)], env=env, timeout=30, check=False)
        ok("a second launch hands over and exits", second.returncode == 0)
        ok("the first one is still running", proc.poll() is None)

        started = time.monotonic()
        quit_code = subprocess.run([str(exe), "--quit"], env=env, timeout=60, check=False)
        ok("--quit reports success", quit_code.returncode == 0)
        proc.wait(timeout=30)
        took = time.monotonic() - started
        ok("the app exited cleanly", proc.returncode == 0)
        ok("quitting is quick", took < QUIT_LIMIT_S, f"{took:.1f}s")
        proc = None

        print("Starting hidden", flush=True)
        with sqlite3.connect(home / "motwi.db") as db:  # as if setup were finished
            db.execute(
                "INSERT INTO settings(id, json) VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET json=excluded.json",
                (json.dumps({"setup_complete": True}),),
            )
        db.close()
        proc = subprocess.Popen([str(exe), "--background"], env=env)
        wait_for("the API", lambda: http("/api/ping")[0] == 200)
        wait_for(
            "the hidden window's page",
            lambda: log_text(home).count("event socket opened (window)") >= 2,
            timeout=45,
        )
        ok("--background runs with no visible window", not visible_window(proc.pid))
        http("/api/show", method="POST", headers={"X-Motwi": "1"})
        wait_for("the window to show", lambda: visible_window(proc.pid), timeout=10)
        ok("the tray's Open can show it", True)
        subprocess.run([str(exe), "--quit"], env=env, timeout=60, check=False)
        proc.wait(timeout=30)
        proc = None
        log = log_text(home)
        ok("no errors in its log", " ERROR " not in log, _errors(log))

        other = tmp / "wiped"
        (other / "logs").mkdir(parents=True)
        (other / "motwi.db").write_text("x", encoding="utf-8")
        subprocess.run(
            [str(exe), "--wipe"], env={**env, "MOTWI_HOME": str(other)}, timeout=60, check=True
        )
        ok("--wipe removes the data folder", not (other / "motwi.db").exists())

        print("Uninstalling", flush=True)
        (home / "keep.txt").write_text("the user's", encoding="utf-8")
        uninstaller = app_dir / "unins000.exe"
        subprocess.run(
            [str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
            env=env,
            check=True,
            timeout=120,
        )
        # the uninstaller hands off to a temp copy of itself, so its exit comes first
        wait_for("the uninstaller to finish", lambda: not exe.exists(), timeout=60)
        wait_for("the Run value to go", lambda: reg_value(RUN_KEY, "Motwi") is None, timeout=30)
        ok("files removed", not exe.exists())
        ok("Run value removed", reg_value(RUN_KEY, "Motwi") is None)
        ok("uninstaller entry removed", reg_value(UNINSTALL_KEY, "DisplayName") is None)
        ok("Start menu shortcut removed", not (start_menu / "Motwi.lnk").exists())
        ok("user data left alone", (home / "motwi.db").is_file())
        print("\nInstaller round trip passed.")
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
        subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", str(tmp)], check=False)


def _errors(log: str) -> str:
    lines = [line for line in log.splitlines() if " ERROR " in line]
    return "\n".join(lines[:5])


if __name__ == "__main__":
    main()
