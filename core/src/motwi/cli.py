"""Command line entry point: `motwi [app | login | logout | set-key | run | simulate]`."""

import argparse
import asyncio
import getpass
import logging
import os
import sys
from pathlib import Path

from motwi.sim.run import simulate

log = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="motwi", description="Motwi: AI chat moderation. With no command, opens the app."
    )
    sub = parser.add_subparsers(dest="command", metavar="command")
    sub.add_parser("app", help="open the Motwi window (the default)")

    login = sub.add_parser("login", help="connect your Twitch account (or a bot account)")
    login.add_argument("--bot", action="store_true", help="connect a separate bot account")
    logout = sub.add_parser("logout", help="forget a connected Twitch account")
    logout.add_argument("--bot", action="store_true", help="forget the bot account")
    key = sub.add_parser("set-key", help="store an AI provider key in Windows Credential Manager")
    key.add_argument("--backup", action="store_true", help="key for the backup provider")

    run = sub.add_parser("run", help="moderate your channel live")
    run.add_argument(
        "--suggest-only", action="store_true", help="suggest actions instead of taking them"
    )

    sim = sub.add_parser("simulate", help="replay a chat script through the engine")
    sim.add_argument("path", type=Path, help="JSON Lines chat script")
    sim.add_argument(
        "--ai",
        choices=("fake", "env"),
        default="fake",
        help="fake: built-in keyword scorer; env: real model from MOTWI_AI_* variables",
    )
    sim.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="playback speed multiplier; 0 replays instantly (default: 1)",
    )
    sim.add_argument(
        "--suggest-only",
        action="store_true",
        help="record suggestions instead of acting",
    )
    return parser


def _fail(message: str, code: int = 2) -> int:
    print(f"motwi: {message}", file=sys.stderr)
    return code


def _simulate(args: argparse.Namespace) -> int:
    if not args.path.is_file():
        return _fail(f"no such file: {args.path}")
    asyncio.run(
        simulate(
            args.path,
            ai=args.ai,
            speed=args.speed,
            suggest_only=args.suggest_only,
            out=sys.stdout,
        )
    )
    return 0


def _twitch(args: argparse.Namespace) -> int:
    from motwi import runner
    from motwi.logs import setup_logging
    from motwi.paths import log_dir
    from motwi.twitch.tokens import KeyringSecrets

    secrets = KeyringSecrets()
    role = "bot" if getattr(args, "bot", False) else "streamer"
    if args.command == "logout":
        gone = runner.logout(role, secrets)
        print(f"Forgot the {role} account." if gone else f"No {role} account was connected.")
        return 0
    if args.command == "set-key":
        slot = "backup" if args.backup else "primary"
        runner.set_key(slot, getpass.getpass(f"Paste the {slot} AI key (hidden): "), secrets)
        print(f"Saved the {slot} AI key.")
        return 0
    setup_logging(log_dir())
    if args.command == "login":
        asyncio.run(runner.login(role, secrets=secrets, out=sys.stdout))
        return 0
    try:
        asyncio.run(
            runner.run_live(
                secrets=secrets, env=os.environ, out=sys.stdout, suggest_only=args.suggest_only
            )
        )
    except KeyboardInterrupt:
        print("Motwi stopped.")
    return 0


def main(argv: list[str] | None = None) -> int:
    from motwi.twitch.auth import AuthError, AuthExpired
    from motwi.twitch.helix import HelixError

    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in (None, "app"):
        from motwi.desktop import run_app

        return run_app()
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="replace")
    try:
        if args.command == "simulate":
            return _simulate(args)
        return _twitch(args)
    except AuthExpired as e:
        return _fail(f"{e}. Connect Twitch again with `motwi login`.")
    except (AuthError, HelixError) as e:
        log.debug("command failed", exc_info=True)
        return _fail(str(e), 1)
    except ValueError as e:
        return _fail(str(e))
