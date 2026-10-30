"""Looks for a newer Motwi. It only looks and links: nothing is downloaded or installed."""

import re
from collections.abc import Mapping
from dataclasses import dataclass

import httpx

DEFAULT_URL = "https://api.github.com/repos/H4ck3rPr0G4m3r/Motwi/releases/latest"
_VERSION = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?$")


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    notes: str


class UpdateError(Exception):
    """The check itself failed; it says nothing about whether an update exists."""


def update_url(env: Mapping[str, str]) -> str | None:
    """`MOTWI_UPDATE_URL` if set (empty turns the check off), else GitHub Releases."""
    value = env.get("MOTWI_UPDATE_URL")
    if value is None:
        return DEFAULT_URL
    return value.strip() or None


def parse_version(text: object) -> tuple[int, int, int] | None:
    """Plain releases only: a pre-release tag doesn't parse, so it is never offered."""
    match = _VERSION.match(text.strip()) if isinstance(text, str) else None
    if match is None:
        return None
    return (int(match[1]), int(match[2]), int(match[3] or 0))


def parse_release(data: object) -> Release | None:
    """GitHub's release JSON, or a plain {version, url, notes}. None if it isn't usable."""
    if not isinstance(data, dict):
        return None
    version = data.get("tag_name", data.get("version"))
    url = data.get("html_url", data.get("url"))
    notes = data.get("body", data.get("notes"))
    if data.get("draft") or data.get("prerelease"):
        return None
    if parse_version(version) is None or not isinstance(url, str):
        return None
    if not url.startswith("https://"):  # shown as a link to click; nothing else is safe
        return None
    return Release(version.strip().lstrip("v"), url, notes if isinstance(notes, str) else "")


def is_newer(release: Release, current: str) -> bool:
    latest, mine = parse_version(release.version), parse_version(current)
    return latest is not None and mine is not None and latest > mine


async def fetch_latest(http: httpx.AsyncClient, url: str, current: str) -> Release | None:
    """The latest release, or None when nothing is published (a 404 from GitHub)."""
    try:
        resp = await http.get(
            url,
            headers={"User-Agent": f"Motwi/{current}", "Accept": "application/json"},
            follow_redirects=True,
            timeout=15,
        )
    except httpx.HTTPError as e:
        raise UpdateError("Couldn't reach the update server.") from e
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise UpdateError(f"The update server answered {resp.status_code}.")
    try:
        data = resp.json()
    except ValueError as e:
        raise UpdateError("The update server sent something unreadable.") from e
    release = parse_release(data)
    if release is None:
        raise UpdateError("The update server sent something unreadable.")
    return release
