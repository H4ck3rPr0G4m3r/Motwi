"""A cloudflared quick tunnel: the public https address mods reach Motwi through (spec 5.9).

It needs no account and no open ports. The address is new every time cloudflared starts, so
anything that hands it out listens to `on_change`.

A new address takes about 20 seconds to appear in DNS, and a lookup made before then is
remembered as "no such site" for minutes. So the address is only offered once it resolves,
asked over DNS-over-HTTPS because that can't leave a bad answer in this machine's cache.
"""

import asyncio
import ctypes
import logging
import re
import subprocess
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)

TunnelState = Literal["off", "starting", "up", "failed"]

_URL = re.compile(r"https://(?!api\.)[a-z0-9-]+\.trycloudflare\.com")
_READY = "Registered tunnel connection"
RETRY_DELAYS_S = (2, 5, 15, 30, 60)
STOP_WAIT_S = 3
CONFIG = "no-autoupdate: true\n"
DNS_URL = "https://cloudflare-dns.com/dns-query"
DNS_POLL_S = 2
DNS_WAIT_S = 45  # past this, offer it anyway: the check itself may be what's blocked


class TunnelLike(Protocol):
    state: TunnelState
    url: str | None
    detail: str
    on_change: Callable[[], None]

    async def start(self, port: int) -> None: ...

    async def stop(self) -> None: ...


def _die_with_us(pid: int) -> object | None:
    """Tie a child to this process, so even a crash can't leave it running. The handle has to
    stay open: closing it, which Windows also does when we die, is what kills the child."""
    if sys.platform != "win32":
        return None

    class Basic(ctypes.Structure):
        _fields_ = [
            ("per_process", ctypes.c_int64),
            ("per_job", ctypes.c_int64),
            ("limit_flags", ctypes.c_uint32),
            ("min_ws", ctypes.c_size_t),
            ("max_ws", ctypes.c_size_t),
            ("active", ctypes.c_uint32),
            ("affinity", ctypes.c_size_t),
            ("priority", ctypes.c_uint32),
            ("scheduling", ctypes.c_uint32),
        ]

    class Extended(ctypes.Structure):
        _fields_ = [
            ("basic", Basic),
            ("io", ctypes.c_uint64 * 6),
            ("process_memory", ctypes.c_size_t),
            ("job_memory", ctypes.c_size_t),
            ("peak_process", ctypes.c_size_t),
            ("peak_job", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.SetInformationJobObject.argtypes = (
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    )
    kernel32.AssignProcessToJobObject.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = Extended()
    info.basic.limit_flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    process = kernel32.OpenProcess(0x0101, False, pid)  # PROCESS_TERMINATE | PROCESS_SET_QUOTA
    ok = (
        process
        and kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
        and kernel32.AssignProcessToJobObject(job, process)
    )
    if process:
        kernel32.CloseHandle(process)
    if not ok:
        log.warning("couldn't tie cloudflared to Motwi (error %s)", ctypes.get_last_error())
        kernel32.CloseHandle(job)
        return None
    return job


async def _resolves(url: str) -> bool:
    async with httpx.AsyncClient(timeout=5) as http:
        resp = await http.get(
            DNS_URL,
            params={"name": urlsplit(url).hostname, "type": "A"},
            headers={"Accept": "application/dns-json"},
        )
        return bool(resp.json().get("Answer"))


class Tunnel:
    def __init__(
        self,
        exe: Path | None,
        config: Path,
        *,
        on_change: Callable[[], None] = lambda: None,
        resolves: Callable[[str], Awaitable[bool]] = _resolves,
    ) -> None:
        self._exe = exe
        self._config = config
        self._resolves = resolves
        self.on_change = on_change
        self.state: TunnelState = "off"
        self.url: str | None = None
        self.detail = ""
        self._task: asyncio.Task | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._job: object | None = None

    def _set(self, state: TunnelState, url: str | None, detail: str = "") -> None:
        if (state, url, detail) != (self.state, self.url, self.detail):
            self.state, self.url, self.detail = state, url, detail
            self.on_change()

    async def start(self, port: int) -> None:
        if self._task is not None and not self._task.done():
            return
        self._set("starting", None)
        self._task = asyncio.create_task(self._run(port))

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self._end_process()
        self._set("off", None)

    async def _announce_when_reachable(self, url: str) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + DNS_WAIT_S
        while loop.time() < deadline:
            try:
                if await self._resolves(url):
                    break
            except (httpx.HTTPError, ValueError) as e:
                log.info("couldn't check the tunnel's address: %s", e)
            await asyncio.sleep(DNS_POLL_S)
        log.info("tunnel is up")
        self._set("up", url)

    async def _end_process(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), STOP_WAIT_S)
            except TimeoutError:
                proc.kill()
                await proc.wait()
        self._job = None

    async def _run(self, port: int) -> None:
        if self._exe is None or not self._exe.is_file():
            self._set("failed", None, "cloudflared is missing from this copy of Motwi.")
            return
        attempt = 0
        while True:
            if await self._once(port):
                attempt = 0
            delay = RETRY_DELAYS_S[min(attempt, len(RETRY_DELAYS_S) - 1)]
            attempt += 1
            log.warning("cloudflared stopped; trying again in %ss", delay)
            self._set("failed" if attempt > 1 else "starting", None, self.detail)
            await asyncio.sleep(delay)

    async def _once(self, port: int) -> bool:
        """One run of cloudflared, until it exits. True if the tunnel came up at all."""
        assert self._exe is not None
        self._config.write_text(CONFIG, encoding="utf-8")
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            self._proc = proc = await asyncio.create_subprocess_exec(
                str(self._exe),
                "tunnel",
                "--config",
                str(self._config),
                "--url",
                f"http://127.0.0.1:{port}",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                creationflags=flags,
            )
        except OSError as e:
            log.warning("couldn't start cloudflared: %s", e)
            self._set("failed", None, "Motwi couldn't start cloudflared.")
            return False
        self._job = _die_with_us(proc.pid)
        url: str | None = None
        up = False
        announce: asyncio.Task | None = None
        assert proc.stderr is not None
        try:
            while True:
                try:
                    raw = await proc.stderr.readline()
                except ValueError:  # one absurdly long line; keep reading
                    continue
                if not raw:
                    break
                line = raw.decode("utf-8", "replace")
                if url is None and (found := _URL.search(line)):
                    url = found[0]
                if url is not None and not up and _READY in line:
                    up = True
                    announce = asyncio.create_task(self._announce_when_reachable(url))
            await proc.wait()
        finally:
            if announce is not None:
                announce.cancel()
            await self._end_process()
        if not up:
            self.detail = "Motwi can't reach Cloudflare right now. It keeps trying."
        return up
