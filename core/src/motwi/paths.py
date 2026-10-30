"""Where Motwi keeps its data on this machine."""

import os
import sys
from pathlib import Path


def data_dir() -> Path:
    r"""`MOTWI_HOME` if set, else `%APPDATA%\Motwi`, else `~/.motwi`. Created on demand."""
    override = os.environ.get("MOTWI_HOME")
    if override:
        path = Path(override)
    elif os.environ.get("APPDATA"):
        path = Path(os.environ["APPDATA"]) / "Motwi"
    else:
        path = Path.home() / ".motwi"
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "motwi.db"


def log_dir() -> Path:
    path = data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ui_dir() -> Path:
    """The built window UI: `MOTWI_UI_DIR`, the frozen app's bundle, or the repo's `ui/dist`."""
    override = os.environ.get("MOTWI_UI_DIR")
    if override:
        return Path(override)
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        return Path(bundle) / "ui"
    return Path(__file__).resolve().parents[3] / "ui" / "dist"
