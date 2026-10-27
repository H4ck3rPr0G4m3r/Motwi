"""Command line entry point: `motwi simulate PATH`."""

import argparse
import asyncio
import sys
from pathlib import Path

from motwi.sim.run import simulate


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="motwi", description="Motwi core engine tools.")
    sub = parser.add_subparsers(dest="command", metavar="command")
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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command != "simulate":
        parser.print_help()
        return 0
    if not args.path.is_file():
        print(f"motwi: no such file: {args.path}", file=sys.stderr)
        return 2
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="replace")
    try:
        asyncio.run(
            simulate(
                args.path,
                ai=args.ai,
                speed=args.speed,
                suggest_only=args.suggest_only,
                out=sys.stdout,
            )
        )
    except ValueError as e:
        print(f"motwi: {e}", file=sys.stderr)
        return 2
    return 0
