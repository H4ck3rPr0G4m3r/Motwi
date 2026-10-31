"""A demo of the app window over sample data, for UI work and screenshots.

    uv run python -m motwi.sim.demo [--setup] [--empty] [--port 47900] [--channel name]

It uses a throwaway data folder and an in-memory credential store, never your real
Motwi data or Twitch account. Twitch is faked as connected, and buttons act on a fake
that only logs. `--channel` sets which channel the extension activates on.

For extension work, two extra routes take the demo token: `POST /api/demo/action` adds an
action as if Motwi had just decided it, and `POST /api/demo/fail` makes the fake Twitch
refuse calls.
"""

import argparse
import asyncio
import random
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from aiohttp import web

from motwi.api import ApiAuth, create_app, serve
from motwi.app import seed_if_empty
from motwi.engine.offenses import OffenseTracker
from motwi.events import ActionChanged
from motwi.models import Ceiling, Kind, Source, Status, StreamContext
from motwi.moderation import ExecutorError, ModerationService
from motwi.paths import ui_dir
from motwi.recap import build_recap
from motwi.service import Motwi, _Session
from motwi.settings import ProviderConfig
from motwi.store import NewAction, Store, Usage
from motwi.twitch.tokens import MemorySecrets, Token, TokenStore

TOKEN = "demo"

CHATTERS = ["grizzlecat", "xx_v0id_xx", "pastaprophet", "lumenfox", "brakkenhoff", "kiwi_drift"]
SAMPLES = [
    (
        Kind.DELETE,
        None,
        Source.SPAM,
        "cheap viewers at strmboost .shop",
        "Spam filter: link spam",
        None,
    ),
    (
        Kind.WARN,
        None,
        Source.AI,
        "the boss is at the end of the left path lol",
        "Spoils a boss location",
        6.4,
    ),
    (
        Kind.TIMEOUT,
        60,
        Source.AI,
        "ur trash at this game, uninstall",
        "Insults the streamer harshly",
        7.8,
    ),
    (
        Kind.TIMEOUT,
        600,
        Source.AI,
        "you are all braindead in this chat",
        "Insults other chatters",
        8.1,
    ),
    (
        Kind.DELETE,
        None,
        Source.BLOCKED_WORD,
        "follow for follow viewbot",
        "Blocked word: viewbot",
        None,
    ),
    (Kind.BAN_REQUEST, None, Source.AI, "go back to where you came from", "Targeted hate", 9.4),
]


async def seed(store: Store) -> None:
    rules = await store.list_rules()
    spoiler = await store.add_rule("No spoilers for the ending", Ceiling.DELETE, "stream")
    await store.add_blocked("strmboost")
    await store.add_blocked("viewbot", ceiling=Ceiling.DELETE)
    await store.add_blocked("slurword", ceiling=Ceiling.BAN, hidden=True)
    await store.add_blocked("slurword2", ceiling=Ceiling.BAN, hidden=True)
    await store.add_person("vip", "lumenfox")
    await store.add_person("trusted", "brakkenhoff")

    now = time.time()
    rng = random.Random(7)
    day = 86400
    for d in (6, 4, 3, 1, 0):
        sid = f"demo-{d}"
        start = now - d * day - 3 * 3600
        await store.stream_started(
            sid, start, "blind first run, no spoilers pls", "Elden Ring Nightreign"
        )
        if d:
            await store.stream_ended(sid, start + 3 * 3600)
        await store.add_checked(
            {(time.strftime("%Y-%m-%d", time.localtime(start)), sid): rng.randint(1800, 4200)}
        )
        for _ in range(rng.randint(14, 34)):
            kind, dur, source, text, reason, score = rng.choice(SAMPLES)
            if kind is Kind.BAN_REQUEST and rng.random() < 0.6:
                kind, dur, source, text, reason, score = SAMPLES[2]
            rule_id = None
            if source is Source.AI:
                rule_id = spoiler.id if "spoil" in reason.lower() else rng.choice(rules).id
            status = rng.choices([Status.DONE, Status.UNDONE, Status.FAILED], weights=[90, 8, 2])[0]
            if kind is Kind.BAN_REQUEST:
                status = rng.choice([Status.DONE, Status.DENIED])
            record = await store.insert_action(
                NewAction(
                    sid,
                    f"id-{text[:4]}",
                    rng.choice(CHATTERS),
                    "m",
                    text,
                    [],
                    source,
                    rule_id,
                    score,
                    reason,
                    kind,
                    True,
                    dur,
                    None,
                    status,
                    "official_h4ck3r" if status in (Status.UNDONE, Status.DENIED) else None,
                    False,
                    "Twitch: user is a moderator" if status is Status.FAILED else None,
                )
            )
            ts = start + rng.uniform(0, 3 * 3600)
            await store._write(
                lambda c, i=record.id, t=ts: c.execute(
                    "UPDATE actions SET created_at=?, updated_at=? WHERE id=?", (t, t, i)
                )
            )
    for d in (6, 4, 3, 1, 0):
        await store.record_usage(
            Usage(
                now - d * day,
                "gemini",
                "gemini-2.5-flash-lite",
                90_000,
                8_000,
                0.012,
                800,
                f"demo-{d}",
            )
        )
    # the last finished stream has a recap; demo-1 was on the backup AI for a while
    await store.ai_state_changed("degraded", "Gemini timing out", now - day - 3 * 3600 + 1800)
    await store.ai_state_changed("ok", "", now - day - 3 * 3600 + 2400)
    finished = await store.get_stream("demo-1")
    if finished is not None:
        await store.save_recap("demo-1", await build_recap(store, finished))
    # two open ban requests right now
    for login, text in (("xx_v0id_xx", "go back to where you came from"), ("kiwi_drift", "kys")):
        await store.insert_action(
            NewAction(
                "demo-0",
                f"id-{login}",
                login,
                f"m-{login}",
                text,
                [],
                Source.AI,
                rules[0].id,
                9.4,
                "Targeted hate",
                Kind.BAN_REQUEST,
                True,
                None,
                3,
                Status.PENDING,
                None,
                True,
                None,
            )
        )


class FakeTwitch:
    """Stands in for Helix so the app's buttons work. `failing` makes every call refuse."""

    def __init__(self) -> None:
        self.failing = False

    async def _call(self, what: str) -> None:
        print(f"fake Twitch: {what}", flush=True)
        if self.failing:
            raise ExecutorError("Twitch said no (demo)")

    async def delete_message(self, message_id: str) -> None:
        await self._call(f"delete {message_id}")

    async def warn(self, user_id: str, reason: str) -> None:
        await self._call(f"warn {user_id}")

    async def send_chat(self, text: str) -> None:
        await self._call(f"chat {text}")

    async def timeout(self, user_id: str, seconds: int, reason: str) -> None:
        await self._call(f"timeout {user_id} {seconds}s")

    async def ban(self, user_id: str, reason: str) -> None:
        await self._call(f"ban {user_id}")

    async def unban(self, user_id: str) -> None:
        await self._call(f"unban {user_id}")

    async def banned_users(self, _broadcaster_id: str) -> list[dict]:
        return []


def fake_session(motwi: Motwi, twitch: FakeTwitch, login: str) -> _Session:
    moderation = ModerationService(
        store=motwi.store,
        executor=twitch,
        bus=motwi.bus,
        live=motwi.live,
        offenses=OffenseTracker(),
    )

    async def reset_clients() -> None:
        pass

    app: Any = SimpleNamespace(moderation=moderation, scorer=None)
    app.scorer = SimpleNamespace(reset_clients=reset_clients, try_score=None)
    helix: Any = twitch
    executor: Any = twitch
    return _Session(helix, helix, executor, app, "42", login)


def demo_routes(motwi: Motwi, twitch: FakeTwitch) -> list[web.RouteDef]:
    async def add_action(request: web.Request) -> web.Response:
        d = await request.json()
        kind = Kind(d.get("kind", "delete"))
        status = Status(d.get("status", "done"))
        record = await motwi.store.insert_action(
            NewAction(
                motwi.live.stream.stream_id,
                d.get("user_id", f"id-{d['login']}"),
                d["login"],
                d["message_id"],
                d["text"],
                [],
                Source(d.get("source", "ai")),
                d.get("rule_id"),
                d.get("score"),
                d.get("reason", "Demo reason"),
                kind,
                True,
                d.get("duration_s"),
                None,
                status,
                d.get("actor"),
                bool(d.get("hold_applied", status is Status.PENDING)),
                d.get("error"),
            )
        )
        motwi.bus.publish(ActionChanged(record))
        return web.json_response({"id": record.id})

    async def set_failing(request: web.Request) -> web.Response:
        twitch.failing = bool((await request.json()).get("on"))
        return web.json_response({"ok": True})

    return [web.post("/api/demo/action", add_action), web.post("/api/demo/fail", set_failing)]


async def main(port: int, setup: bool, channel: str, empty: bool = False) -> None:
    home = Path(tempfile.mkdtemp(prefix="motwi-demo-"))
    store = await Store.open(home / "motwi.db")
    secrets = MemorySecrets()
    if not setup:
        secrets.set("ai:primary", "demo-key")
        TokenStore(secrets).save("streamer", Token("x", "y", time.time() + 3600, (), "42", channel))
    if not setup:
        await seed_if_empty(store)
        if not empty:
            await seed(store)
        await store.save_settings(
            replace(
                await store.get_settings(),
                setup_complete=True,
                primary=ProviderConfig("gemini", "gemini-2.5-flash-lite"),
            )
        )
    motwi = await Motwi.open(store, secrets=secrets, env={})
    twitch = FakeTwitch()
    if not setup:
        # fake a live connection instead of calling connect(), so nothing reaches Twitch
        motwi._twitch = ("connected", "")
        motwi._session = fake_session(motwi, twitch, channel)
        motwi.live.stream = StreamContext(
            "blind first run, no spoilers pls", "Elden Ring Nightreign", "demo-0"
        )
    app = create_app(
        motwi,
        auth=ApiAuth(TOKEN),
        port=port,
        ui_dir=ui_dir(),
        open_url=lambda url: print(f"pair at {url}", flush=True),
    )
    app.add_routes(demo_routes(motwi, twitch))
    runner = await serve(app, port)
    print(f"Motwi demo: http://127.0.0.1:{port}/#k={TOKEN}", flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        await motwi.aclose()
        await store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=47900)
    parser.add_argument("--setup", action="store_true", help="start at first-run setup")
    parser.add_argument("--channel", default="official_h4ck3r", help="the fake streamer's login")
    parser.add_argument("--empty", action="store_true", help="no sample actions")
    args = parser.parse_args()
    asyncio.run(main(args.port, args.setup, args.channel.lower(), args.empty))
