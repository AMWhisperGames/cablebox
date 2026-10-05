"""Paths and logging that do not need Qt or mpv."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def default_library_path() -> Path:
    return project_root() / "data" / "channels.json"


def prepare_mpv_search_path() -> None:
    """Let python-mpv find a library shipped next to the app."""
    root = project_root()
    for folder in (root / "mpv", root):
        names = ("mpv-2.dll", "libmpv-2.dll", "mpv-1.dll", "libmpv.so.2", "libmpv.so")
        if not any((folder / name).exists() for name in names):
            continue
        os.environ["PATH"] = str(folder) + os.pathsep + os.environ.get("PATH", "")
        if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
            os.add_dll_directory(str(folder))
        return


def configure_logging(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(path, encoding="utf-8"),
        ],
    )
