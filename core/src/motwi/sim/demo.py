"""A demo of the app window over sample data, for UI work and screenshots.

    uv run python -m motwi.sim.demo [--setup] [--port 47900]

It uses a throwaway data folder and an in-memory credential store, never your real
Motwi data or Twitch account. Twitch is faked as connected; buttons that need Twitch
report that it isn't.
"""

import argparse
import asyncio
import random
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from motwi.api import ApiAuth, create_app, serve
from motwi.app import seed_if_empty
from motwi.models import Ceiling, Kind, Source, Status, StreamContext
from motwi.paths import ui_dir
from motwi.service import Motwi
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


async def main(port: int, setup: bool) -> None:
    home = Path(tempfile.mkdtemp(prefix="motwi-demo-"))
    store = await Store.open(home / "motwi.db")
    secrets = MemorySecrets()
    secrets.set("ai:primary", "demo-key")
    if not setup:
        TokenStore(secrets).save(
            "streamer", Token("x", "y", time.time() + 3600, (), "42", "official_h4ck3r")
        )
    if not setup:
        await seed_if_empty(store)
        await seed(store)
        await store.save_settings(
            replace(
                await store.get_settings(),
                setup_complete=True,
                primary=ProviderConfig("gemini", "gemini-2.5-flash-lite"),
            )
        )
    motwi = await Motwi.open(store, secrets=secrets, env={})
    if not setup:
        # fake a live connection instead of calling connect(), so nothing reaches Twitch
        motwi._twitch = ("connected", "")
        motwi.live.stream = StreamContext(
            "blind first run, no spoilers pls", "Elden Ring Nightreign", "demo-0"
        )
    app = create_app(motwi, auth=ApiAuth(TOKEN), port=port, ui_dir=ui_dir())
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
    args = parser.parse_args()
    asyncio.run(main(args.port, args.setup))
