"""Launch the cable box."""

from __future__ import annotations

import argparse
import locale
import logging
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from cablebox.bootstrap import configure_logging, default_library_path, prepare_mpv_search_path
from cablebox.library import LibraryError, load_library

log = logging.getLogger(__name__)

MPV_HELP = (
    "Cable Box needs mpv's library (mpv-2.dll on Windows, libmpv on the Pi).\n\n"
    "Windows: download a current mpv build, unzip it, and add that folder to PATH.\n"
    "Raspberry Pi OS: sudo apt install mpv libmpv2"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cable box simulator")
    parser.add_argument("--fullscreen", action="store_true", help="start fullscreen for a TV or Pi")
    parser.add_argument("--data", type=Path, default=None, help="path to channels.json")
    args = parser.parse_args(argv)

    prepare_mpv_search_path()
    data_path = (args.data or default_library_path()).expanduser().resolve()
    configure_logging(data_path.parent / "cablebox.log")

    app = QApplication([])
    app.setApplicationName("Cable Box")
    app.setStyle("Fusion")

    try:
        library = load_library(data_path)
    except LibraryError as exc:
        QMessageBox.critical(None, "Lineup file", str(exc))
        return 1

    # Qt resets the numeric locale on import. libmpv requires the C locale.
    try:
        from cablebox.app import CableBox
    except Exception as exc:
        log.exception("could not start playback")
        QMessageBox.critical(None, "mpv is missing", f"{MPV_HELP}\n\n{exc}")
        return 1

    locale.setlocale(locale.LC_NUMERIC, "C")
    try:
        window = CableBox(library, data_path)
    except Exception as exc:
        log.exception("could not start playback")
        QMessageBox.critical(None, "mpv is missing", f"{MPV_HELP}\n\n{exc}")
        return 1

    window.start(args.fullscreen)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
