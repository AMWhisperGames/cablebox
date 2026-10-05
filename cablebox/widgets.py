"""Watch-screen overlays: standby, static, the channel banner, and volume."""

from __future__ import annotations

import random

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QSlider, QWidget

from cablebox.format import format_range
from cablebox.schedule import Airing, Channel

BARS = [
    QColor("#c0c0c0"),
    QColor("#c0c000"),
    QColor("#00c0c0"),
    QColor("#00c000"),
    QColor("#c000c0"),
    QColor("#c00000"),
    QColor("#0000c0"),
]


class StandbyCard(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._number = ""
        self._heading = "Please stand by"
        self._detail = ""
        self.setAutoFillBackground(True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hide()

    def show_card(self, number: str, heading: str, detail: str) -> None:
        self._number = number
        self._heading = heading
        self._detail = detail
        self.show()
        self.raise_()
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#05080f"))
        bar_height = max(48, int(self.height() * 0.18))
        bar_width = self.width() / len(BARS)
        for index, color in enumerate(BARS):
            painter.fillRect(int(index * bar_width), 0, int(bar_width) + 1, bar_height, color)
        painter.setPen(QColor("#f2f6fa"))
        painter.setFont(_font(64, bold=True))
        painter.drawText(0, bar_height, self.width(), int(self.height() * 0.34), Qt.AlignmentFlag.AlignCenter, self._heading)
        if self._number:
            painter.setFont(_font(42, bold=True))
            painter.setPen(QColor("#f5c518"))
            painter.drawText(
                0,
                bar_height + int(self.height() * 0.28),
                self.width(),
                70,
                Qt.AlignmentFlag.AlignCenter,
                self._number,
            )
        painter.setFont(_font(22))
        painter.setPen(QColor("#c5d4e8"))
        painter.drawText(40, int(self.height() * 0.68), self.width() - 80, 120, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, self._detail)
        painter.setFont(_font(16))
        painter.drawText(
            0,
            self.height() - 64,
            self.width(),
            40,
            Qt.AlignmentFlag.AlignCenter,
            "Up/Down channel    G guide    M lineup",
        )
        painter.end()


class StaticOverlay(QWidget):
    """A short burst of television snow, shown while the channel changes."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._frames_left = 0
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hide()

    def flash(self) -> None:
        self._frames_left = 8
        self.show()
        self.raise_()
        self._timer.start()
        self.update()

    def _tick(self) -> None:
        self._frames_left -= 1
        if self._frames_left <= 0:
            self._timer.stop()
            self.hide()
            return
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(12, 12, 12))
        width = max(1, self.width())
        height = max(1, self.height())
        rng = random.Random()
        for _ in range(90):
            y = rng.randrange(height)
            band = rng.randrange(1, 5)
            shade = rng.randrange(30, 255)
            painter.fillRect(0, y, width, band, QColor(shade, shade, shade))
        for _ in range(140):
            shade = rng.randrange(80, 255)
            painter.fillRect(
                rng.randrange(width),
                rng.randrange(height),
                rng.randrange(2, 10),
                rng.randrange(2, 7),
                QColor(shade, shade, shade),
            )
        painter.end()


class VolumeControl(QWidget):
    """Minus, slider, plus, and mute. Emits the level the player should use."""

    changed = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("VolumeControl")
        self._level = 70
        self._muted = False
        self._loading = False
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, True)
        self.setStyleSheet(
            """
            QWidget#VolumeControl { background: rgba(6, 16, 24, 220); border: 1px solid #1e3a5f; }
            QPushButton {
                background: #163e68; color: #f2f6fa; border: 1px solid #2a6eaa; padding: 4px 8px; font-size: 14px;
            }
            QPushButton:hover { background: #1f568c; }
            QSlider::groove:horizontal { height: 6px; background: #1e3a5f; }
            QSlider::handle:horizontal { background: #f5c518; width: 14px; margin: -5px 0; }
            """
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)
        minus = QPushButton("−")
        minus.setFixedWidth(36)
        minus.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        minus.clicked.connect(lambda: self.nudge(-5))
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 100)
        self._slider.setValue(self._level)
        self._slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._slider.valueChanged.connect(self._on_slider)
        plus = QPushButton("+")
        plus.setFixedWidth(36)
        plus.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        plus.clicked.connect(lambda: self.nudge(5))
        self._mute = QPushButton("Mute")
        self._mute.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._mute.clicked.connect(self.toggle_mute)
        layout.addWidget(minus)
        layout.addWidget(self._slider, stretch=1)
        layout.addWidget(plus)
        layout.addWidget(self._mute)

    def level(self) -> int:
        return self._level

    def set_level(self, level: int) -> None:
        self._level = max(0, min(100, int(level)))
        self._muted = False
        self._mute.setText("Mute")
        self._loading = True
        self._slider.setValue(self._level)
        self._loading = False

    def nudge(self, delta: int) -> None:
        if self._muted:
            self._muted = False
            self._mute.setText("Mute")
        self._level = max(0, min(100, self._level + delta))
        self._loading = True
        self._slider.setValue(self._level)
        self._loading = False
        self.changed.emit(self._level)

    def toggle_mute(self) -> None:
        self._muted = not self._muted
        self._mute.setText("Muted" if self._muted else "Mute")
        self.changed.emit(0 if self._muted else self._level)

    def _on_slider(self, value: int) -> None:
        if self._loading:
            return
        self._level = value
        if self._muted:
            self._muted = False
            self._mute.setText("Mute")
        self.changed.emit(self._level)


class Banner(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._mode = "channel"
        self._number = ""
        self._name = ""
        self._title = ""
        self._when = ""
        self._message = ""
        self._volume = 70
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        self.setAutoFillBackground(True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hide()

    def show_channel(self, channel: Channel, airing: Airing) -> None:
        self._mode = "channel"
        self._number = str(channel.number)
        self._name = channel.name
        self._title = airing.title
        self._when = format_range(airing.start, airing.end)
        self._present(4000)

    def show_entry(self, digits: str) -> None:
        self._mode = "entry"
        self._message = digits
        self._timer.stop()
        self.show()
        self.raise_()
        self.update()

    def show_message(self, message: str) -> None:
        self._mode = "message"
        self._message = message
        self._present(4000)

    def hide_entry(self) -> None:
        if self._mode == "entry":
            self.hide()

    def show_volume(self, level: int) -> None:
        self._mode = "volume"
        self._volume = level
        self._present(1500)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#061018"))
        painter.fillRect(0, 0, self.width(), 4, QColor("#f5c518"))
        if self._mode == "channel":
            self._paint_channel(painter)
        elif self._mode == "entry":
            self._paint_message(painter, self._message, "Press Enter to tune")
        elif self._mode == "volume":
            self._paint_volume(painter)
        else:
            self._paint_message(painter, self._message, "")
        painter.end()

    def _paint_channel(self, painter: QPainter) -> None:
        painter.setPen(QColor("#f5c518"))
        painter.setFont(_font(48, bold=True))
        painter.drawText(28, 16, 120, 84, Qt.AlignmentFlag.AlignVCenter, self._number)
        painter.setPen(QColor("#f2f6fa"))
        painter.setFont(_font(22, bold=True))
        painter.drawText(160, 18, self.width() - 360, 36, Qt.AlignmentFlag.AlignVCenter, self._name)
        painter.setFont(_font(26))
        painter.drawText(160, 56, self.width() - 360, 40, Qt.AlignmentFlag.AlignVCenter, self._title)
        painter.setPen(QColor("#c5d4e8"))
        painter.setFont(_font(18))
        painter.drawText(160, 104, self.width() - 360, 32, Qt.AlignmentFlag.AlignVCenter, self._when)
        painter.setFont(_font(15))
        painter.drawText(self.width() - 280, 104, 250, 32, int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), "G Guide    M Lineup")

    def _paint_message(self, painter: QPainter, primary: str, secondary: str) -> None:
        painter.setPen(QColor("#f2f6fa"))
        painter.setFont(_font(48, bold=True))
        painter.drawText(28, 20, self.width() - 56, 70, Qt.AlignmentFlag.AlignVCenter, primary)
        if secondary:
            painter.setPen(QColor("#c5d4e8"))
            painter.setFont(_font(18))
            painter.drawText(28, 96, self.width() - 56, 36, Qt.AlignmentFlag.AlignVCenter, secondary)

    def _paint_volume(self, painter: QPainter) -> None:
        painter.setPen(QColor("#f2f6fa"))
        painter.setFont(_font(28, bold=True))
        painter.drawText(28, 24, 220, 40, Qt.AlignmentFlag.AlignVCenter, f"Volume  {self._volume}")
        track = self.rect().adjusted(28, 88, -28, -36)
        painter.fillRect(track, QColor("#1e3a5f"))
        filled = track.adjusted(0, 0, -int(track.width() * (100 - self._volume) / 100), 0)
        painter.fillRect(filled, QColor("#f5c518"))
        painter.setPen(QPen(QColor("#f5c518"), 1))

    def _present(self, milliseconds: int) -> None:
        self.show()
        self.raise_()
        self.update()
        self._timer.start(milliseconds)


def _font(size: int, bold: bool = False) -> QFont:
    font = QFont()
    font.setFamilies(["Segoe UI", "DejaVu Sans", "Liberation Sans", "Sans Serif"])
    font.setPixelSize(size)
    font.setBold(bold)
    return font
