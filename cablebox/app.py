"""Cable box window: playback, guide, lineup editor, and the remote keys."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QKeyEvent, QShortcut, QKeySequence
from PySide6.QtWidgets import QApplication, QMainWindow, QStackedWidget, QWidget

from cablebox.editor import EditorPage
from cablebox.guide import GuideWidget
from cablebox.library import Library, save_library
from cablebox.player import MpvWidget
from cablebox.schedule import Channel, find_channel, now_playing, step_channel
from cablebox.widgets import Banner, StandbyCard, StaticOverlay, VolumeControl

log = logging.getLogger(__name__)

DRIFT_SECONDS = 3.0
LOAD_GRACE_SECONDS = 2.0


class WatchPage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.player = MpvWidget(self)
        self.standby = StandbyCard(self)
        self.static = StaticOverlay(self)
        self.banner = Banner(self)
        self.volume = VolumeControl(self)
        self.guide = GuideWidget(self)
        self.guide.hide()
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(self.backgroundRole(), QColor("#000000"))
        self.setPalette(palette)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        rect = self.rect()
        self.player.setGeometry(rect)
        self.standby.setGeometry(rect)
        self.static.setGeometry(rect)
        self.guide.setGeometry(rect)
        banner_height = min(168, rect.height())
        self.banner.setGeometry(0, rect.height() - banner_height, rect.width(), banner_height)
        control_width = min(320, max(220, rect.width() // 4))
        self.volume.setGeometry(rect.width() - control_width - 16, 16, control_width, 48)
        self.restack()

    def restack(self) -> None:
        self.player.lower()
        if self.standby.isVisible():
            self.standby.raise_()
        if self.static.isVisible():
            self.static.raise_()
        if self.banner.isVisible():
            self.banner.raise_()
        self.volume.raise_()
        if self.guide.isVisible():
            self.guide.raise_()


class CableBox(QMainWindow):
    def __init__(self, library: Library, data_path: Path) -> None:
        super().__init__()
        self.library = library
        self.data_path = data_path
        self._channel_number = _initial_channel(library)
        self._editor_open = False
        self._loaded_path: str | None = None
        self._force_load = True
        self._load_started = 0.0
        self._missing_reads = 0
        self._trouble_key: str | None = None
        self._standby_key: tuple[str, str, str] | None = None
        self._digits = ""

        self.watch = WatchPage()
        self.editor = EditorPage(data_path)
        self.editor.saved.connect(self._on_library_saved)
        self.editor.closed.connect(self._close_editor)

        self._stack = QStackedWidget()
        self._stack.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._stack.addWidget(self.watch)
        self._stack.addWidget(self.editor)
        self.setCentralWidget(self._stack)

        self.setWindowTitle("Cable Box")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.resize(1280, 720)
        self.setMinimumSize(960, 540)
        self.watch.guide.set_lineup(library.channels, library.guide_hours, library.videos)
        self.watch.volume.set_level(library.volume)
        self.watch.player.set_volume(library.volume)
        self.watch.volume.changed.connect(self._on_volume_changed)

        self._digit_timer = QTimer(self)
        self._digit_timer.setSingleShot(True)
        self._digit_timer.timeout.connect(self._clear_digits)

        self._volume_timer = QTimer(self)
        self._volume_timer.setSingleShot(True)
        self._volume_timer.timeout.connect(self._persist)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._sync)

        escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        escape.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        escape.activated.connect(self._on_escape)

    def start(self, fullscreen: bool) -> None:
        if fullscreen:
            self.showFullScreen()
        else:
            self.show()
        self.setFocus()
        self._timer.start()
        QTimer.singleShot(0, self._announce_current)

    def closeEvent(self, event) -> None:
        if QApplication.activeModalWidget() is None and self._editor_open and not self.editor.confirm_close():
            event.ignore()
            return
        self._timer.stop()
        self.library.last_channel = self._channel_number
        self._persist()
        self.watch.player.shutdown()
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self._editor_open:
            super().keyPressEvent(event)
            return
        if self.watch.guide.isVisible():
            self._guide_key(event.key())
            event.accept()
            return
        self._watch_key(event)
        event.accept()

    def _announce_current(self) -> None:
        self._sync()
        if not self.watch.standby.isVisible():
            self._show_banner()

    def _current_channel(self) -> Channel | None:
        if self._channel_number is None:
            return None
        return find_channel(self.library.channels, self._channel_number)

    def _sync(self) -> None:
        now = datetime.now()
        self.watch.guide.set_now(now)
        if self._editor_open or self.watch.guide.isVisible():
            return
        channel = self._current_channel()
        if channel is None:
            self._stop_playback()
            self._show_standby("", "No channels yet", "Press M to program the lineup.")
            return
        airing = now_playing(channel, now, self.library.videos)
        path = airing.path
        if airing.is_standby or not path or not Path(path).is_file():
            self._stop_playback()
            if airing.is_standby:
                detail = "Please stand by." if airing.title == "Please stand by" else airing.title
                self._show_standby(str(channel.number), channel.name, detail)
            else:
                if self._trouble_key != path:
                    log.warning("missing video %s", path)
                    self._trouble_key = path
                self._show_standby(
                    str(channel.number),
                    airing.title,
                    "Technical difficulties. The video file is missing.",
                )
            return

        self._trouble_key = None
        self._standby_key = None
        if self.watch.standby.isVisible():
            self.watch.standby.hide()
        if self._loaded_path != path or self._force_load:
            log.info("channel %s offset %.3f file %s", channel.number, airing.offset, path)
            try:
                self.watch.player.play(path, airing.offset)
            except Exception:
                log.exception("playback failed for %s", path)
                self._show_standby(str(channel.number), airing.title, "Technical difficulties.")
                return
            self._loaded_path = path
            self._force_load = False
            self._load_started = time.monotonic()
            self._missing_reads = 0
            return
        if time.monotonic() - self._load_started < LOAD_GRACE_SECONDS:
            return
        position = self.watch.player.playback_time()
        if position is None:
            self._missing_reads += 1
            if self._missing_reads >= 3:
                self._force_load = True
                self._missing_reads = 0
            return
        self._missing_reads = 0
        if abs(position - airing.offset) > DRIFT_SECONDS:
            log.info("correct drift: player %.3f broadcast %.3f", position, airing.offset)
            self.watch.player.seek(airing.offset)

    def _stop_playback(self) -> None:
        if self._loaded_path is not None:
            self.watch.player.stop()
            self._loaded_path = None
            self._force_load = True

    def _show_standby(self, number: str, heading: str, detail: str) -> None:
        key = (number, heading, detail)
        if self.watch.standby.isVisible() and self._standby_key == key:
            return
        self._standby_key = key
        self.watch.banner.hide()
        self.watch.standby.show_card(number, heading, detail)
        self.watch.restack()

    def _show_banner(self) -> None:
        channel = self._current_channel()
        if channel is None:
            self.watch.banner.show_message("No channels. Press M to program the lineup.")
        else:
            self.watch.banner.show_channel(channel, now_playing(channel, datetime.now(), self.library.videos))
        self.watch.restack()

    def _tune(self, number: int) -> None:
        channel = find_channel(self.library.channels, number)
        if channel is None:
            self.watch.banner.show_message(f"No channel {number}")
            self.watch.restack()
            return
        changed = self._channel_number != number
        self._channel_number = number
        self.library.last_channel = number
        self._force_load = True
        self._hide_guide()
        self.watch.player.set_paused(False)
        if changed:
            self.watch.static.flash()
        self._sync()
        self.watch.banner.show_channel(channel, now_playing(channel, datetime.now(), self.library.videos))
        self.watch.restack()
        self._persist()
        self.setFocus()

    def _step(self, direction: int) -> None:
        channel = step_channel(self.library.channels, self._channel_number, direction)
        if channel is None:
            self.watch.banner.show_message("No channels. Press M to program the lineup.")
            self.watch.restack()
            return
        self._tune(channel.number)

    def _nudge_volume(self, delta: int) -> None:
        self.watch.volume.nudge(delta)

    def _on_volume_changed(self, playback: int) -> None:
        self.watch.player.set_volume(playback)
        self.library.volume = self.watch.volume.level()
        self.editor.note_volume(self.library.volume)
        self.watch.banner.show_volume(playback)
        self.watch.restack()
        self._volume_timer.start(400)

    def _push_digit(self, digit: str) -> None:
        self._digits = digit if len(self._digits) >= 4 else self._digits + digit
        self.watch.banner.show_entry(self._digits)
        self.watch.restack()
        self._digit_timer.start(4000)

    def _clear_digits(self) -> None:
        self._digits = ""
        self.watch.banner.hide_entry()

    def _commit_digits(self) -> None:
        if not self._digits:
            return
        number = int(self._digits)
        self._digits = ""
        self._digit_timer.stop()
        self._tune(number)

    def _open_guide(self) -> None:
        if self._editor_open:
            return
        self._clear_digits()
        self.watch.banner.hide()
        self.watch.player.set_paused(True)
        self.watch.guide.set_lineup(self.library.channels, self.library.guide_hours, self.library.videos)
        self.watch.guide.focus_live(datetime.now(), self._channel_number)
        self.watch.restack()
        self.setFocus()

    def _hide_guide(self) -> None:
        self.watch.guide.hide()
        self.watch.restack()

    def _close_guide(self) -> None:
        if not self.watch.guide.isVisible():
            return
        self._hide_guide()
        if self._editor_open:
            return
        self.watch.player.set_paused(False)
        self._force_load = True
        self._sync()
        self.setFocus()

    def _open_editor(self) -> None:
        self._hide_guide()
        self._editor_open = True
        self.watch.player.set_paused(True)
        self.editor.load_from(self.library)
        self._stack.setCurrentWidget(self.editor)
        self.editor.focus_default()

    def _close_editor(self) -> None:
        if not self._editor_open:
            return
        if not self.editor.confirm_close():
            return
        self._editor_open = False
        self._stack.setCurrentWidget(self.watch)
        self.watch.player.set_paused(False)
        self._force_load = True
        self._sync()
        self._show_banner()
        self.setFocus()

    def _on_library_saved(self, library: Library) -> None:
        if self._channel_number is None or find_channel(library.channels, self._channel_number) is None:
            nxt = step_channel(library.channels, None, 1)
            self._channel_number = nxt.number if nxt else None
        library.last_channel = self._channel_number
        self.editor.note_last_channel(self._channel_number)
        self.library = library
        self.watch.guide.set_lineup(library.channels, library.guide_hours, library.videos)
        self._force_load = True
        self._persist()

    def _guide_key(self, key: Qt.Key) -> None:
        guide = self.watch.guide
        if key == Qt.Key.Key_Up:
            guide.move_channel(-1)
        elif key == Qt.Key.Key_Down:
            guide.move_channel(1)
        elif key == Qt.Key.Key_Left:
            guide.move_program(-1)
        elif key == Qt.Key.Key_Right:
            guide.move_program(1)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            number = guide.selected_channel_number()
            if number is None:
                self._close_guide()
            else:
                self._tune(number)
        elif key in (Qt.Key.Key_G, Qt.Key.Key_Escape):
            self._close_guide()
        elif key == Qt.Key.Key_M:
            self._open_editor()
        elif key == Qt.Key.Key_Q:
            self.close()

    def _watch_key(self, event: QKeyEvent) -> None:
        key = event.key()
        text = event.text()
        if text.isdigit() and not event.isAutoRepeat():
            self._push_digit(text)
            return
        if event.isAutoRepeat() and key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            return
        if key == Qt.Key.Key_Up:
            self._step(1)
        elif key == Qt.Key.Key_Down:
            self._step(-1)
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_VolumeDown, Qt.Key.Key_Minus):
            self._nudge_volume(-5)
        elif key in (Qt.Key.Key_Right, Qt.Key.Key_VolumeUp, Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self._nudge_volume(5)
        elif key == Qt.Key.Key_VolumeMute:
            self.watch.volume.toggle_mute()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self._digits:
                self._commit_digits()
            else:
                self._show_banner()
        elif key == Qt.Key.Key_G:
            self._open_guide()
        elif key == Qt.Key.Key_M:
            self._open_editor()
        elif key == Qt.Key.Key_I:
            self._show_banner()
        elif key == Qt.Key.Key_F:
            self._toggle_fullscreen()
        elif key == Qt.Key.Key_Q:
            self.close()

    def _on_escape(self) -> None:
        if QApplication.activeModalWidget() is not None:
            return
        if self._editor_open:
            self._close_editor()
            return
        if self.watch.guide.isVisible():
            self._close_guide()
            return
        if self._digits:
            self._clear_digits()
            return
        if self.isFullScreen():
            self.showNormal()

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _persist(self) -> None:
        try:
            save_library(self.data_path, self.library)
        except OSError:
            log.exception("could not save lineup")


def _initial_channel(library: Library) -> int | None:
    if library.last_channel is not None and find_channel(library.channels, library.last_channel):
        return library.last_channel
    first = step_channel(library.channels, None, 1)
    return first.number if first else None
