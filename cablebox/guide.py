"""On-screen cable guide drawn over the video."""

from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QWidget

from cablebox.format import format_clock, format_range
from cablebox.schedule import (
    Airing,
    Channel,
    Video,
    find_channel,
    floor_half_hour,
    guide_blocks,
    midnight_of,
    now_playing,
    sorted_channels,
)

HEADER_HEIGHT = 58
FOOTER_HEIGHT = 108
LEFT_WIDTH = 248
ROW_MIN = 58

BG = QColor("#07111f")
HEADER_BG = QColor("#0c2340")
CHANNEL_BG = QColor("#0a1929")
ROW_BG = QColor("#0d2138")
ROW_CURRENT = QColor("#123056")
BLOCK = QColor("#1a4a7a")
BLOCK_ALT = QColor("#163e68")
BLOCK_SELECTED = QColor("#245a94")
STANDBY_BLOCK = QColor("#243044")
GOLD = QColor("#f5c518")
NOW = QColor("#ff3b30")
TEXT = QColor("#f2f6fa")
MUTED = QColor("#c5d4e8")
GRID = QColor("#1e3a5f")
FOOTER_BG = QColor("#041018")


class GuideWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.channels: list[Channel] = []
        self.videos: tuple[Video, ...] = ()
        self.guide_hours = 2.0
        self.origin = datetime.now()
        self.now = datetime.now()
        self.selected_number: int | None = None
        self.selected_start: datetime | None = None
        self._row_offset = 0
        self.setAutoFillBackground(True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def set_lineup(self, channels: list[Channel], guide_hours: float, videos: list[Video] | None = None) -> None:
        self.channels = sorted_channels(list(channels))
        self.videos = tuple(videos or [])
        self.guide_hours = guide_hours
        if self.selected_number is not None and find_channel(self.channels, self.selected_number) is None:
            self.selected_number = self.channels[0].number if self.channels else None
            self.selected_start = None
        self.update()

    def focus_live(self, moment: datetime, channel_number: int | None) -> None:
        self.now = moment
        self.origin = floor_half_hour(moment)
        self.channels = sorted_channels(self.channels)
        if not self.channels:
            self.selected_number = None
            self.selected_start = None
        else:
            if channel_number is None or find_channel(self.channels, channel_number) is None:
                channel_number = self.channels[0].number
            self.selected_number = channel_number
            channel = find_channel(self.channels, channel_number)
            assert channel is not None
            self.selected_start = now_playing(channel, moment, self.videos).start
            self._reveal_row()
        self.show()
        self.raise_()
        self.update()

    def set_now(self, moment: datetime) -> None:
        self.now = moment
        if self.isVisible():
            self.update()

    def move_channel(self, direction: int) -> None:
        if not self.channels or self.selected_number is None:
            return
        numbers = [channel.number for channel in self.channels]
        index = numbers.index(self.selected_number)
        index = (index + direction) % len(self.channels)
        channel = self.channels[index]
        self.selected_number = channel.number
        probe = self.selected_start or self.now
        self.selected_start = now_playing(channel, probe, self.videos).start
        self._reveal_row()
        self.update()

    def move_program(self, direction: int) -> None:
        channel = self._selected_channel()
        if channel is None or self.selected_start is None:
            return
        day = midnight_of(self.selected_start)
        window_start = day - timedelta(days=1)
        window_end = day + timedelta(days=2)
        blocks = guide_blocks(channel, window_start, window_end, self.videos)
        playable = [block for block in blocks if not block.is_standby]
        if not playable:
            return
        identities = [self._true_start(channel, block) for block in playable]
        index = _closest_index(identities, self.selected_start)
        index = max(0, min(len(playable) - 1, index + direction))
        self.selected_start = identities[index]
        self._reveal_time(self.selected_start)
        self.update()

    def selected_channel_number(self) -> int | None:
        return self.selected_number

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), BG)
        width = self.width()
        height = self.height()
        if width < 40 or height < HEADER_HEIGHT + FOOTER_HEIGHT + 20:
            painter.end()
            return

        grid_top = HEADER_HEIGHT
        grid_bottom = height - FOOTER_HEIGHT
        grid_height = grid_bottom - grid_top
        grid_width = max(1, width - LEFT_WIDTH)
        painter.fillRect(0, 0, width, HEADER_HEIGHT, HEADER_BG)
        painter.fillRect(0, grid_bottom, width, FOOTER_HEIGHT, FOOTER_BG)

        clock_font = _font(22, bold=True)
        painter.setFont(clock_font)
        painter.setPen(GOLD)
        painter.drawText(16, 0, LEFT_WIDTH - 24, HEADER_HEIGHT, Qt.AlignmentFlag.AlignVCenter, format_clock(self.now))

        if not self.channels:
            painter.setPen(TEXT)
            painter.setFont(_font(20))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No channels. Press M to program the lineup.")
            painter.end()
            return

        visible = timedelta(hours=self.guide_hours)
        self._draw_time_header(painter, grid_width, visible)
        row_height, first_row, last_row = self._row_geometry(grid_height)

        for row_index in range(first_row, last_row):
            channel = self.channels[row_index]
            top = grid_top + (row_index - first_row) * row_height
            self._draw_row(painter, channel, top, row_height, grid_width, visible)

        self._draw_now_line(painter, grid_top, grid_bottom, grid_width, visible)
        self._draw_footer(painter, grid_bottom, width)
        painter.end()

    def _draw_time_header(self, painter: QPainter, grid_width: int, visible: timedelta) -> None:
        painter.setFont(_font(15, bold=True))
        painter.setPen(MUTED)
        step = timedelta(minutes=30)
        cursor = self.origin
        end = self.origin + visible
        seconds = visible.total_seconds()
        while cursor < end:
            x = LEFT_WIDTH + int((cursor - self.origin).total_seconds() / seconds * grid_width)
            painter.drawText(x + 8, 0, 160, HEADER_HEIGHT, Qt.AlignmentFlag.AlignVCenter, format_clock(cursor))
            painter.setPen(QPen(GRID, 1))
            painter.drawLine(x, HEADER_HEIGHT - 10, x, HEADER_HEIGHT - 1)
            painter.setPen(MUTED)
            cursor += step

    def _draw_row(
        self,
        painter: QPainter,
        channel: Channel,
        top: int,
        row_height: int,
        grid_width: int,
        visible: timedelta,
    ) -> None:
        current = channel.number == self.selected_number
        painter.fillRect(0, top, self.width(), row_height - 2, ROW_CURRENT if current else ROW_BG)
        painter.fillRect(0, top, LEFT_WIDTH - 8, row_height - 2, CHANNEL_BG)
        painter.setPen(GOLD if current else TEXT)
        painter.setFont(_font(26, bold=True))
        painter.drawText(8, top, 72, row_height - 2, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight), str(channel.number))
        painter.setFont(_font(16))
        painter.setPen(TEXT)
        name = QFontMetrics(painter.font()).elidedText(channel.name, Qt.TextElideMode.ElideRight, LEFT_WIDTH - 100)
        painter.drawText(88, top, LEFT_WIDTH - 104, row_height - 2, Qt.AlignmentFlag.AlignVCenter, name)

        window_end = self.origin + visible
        blocks = guide_blocks(channel, self.origin, window_end, self.videos)
        seconds = visible.total_seconds()
        for index, block in enumerate(blocks):
            x = LEFT_WIDTH + int((block.start - self.origin).total_seconds() / seconds * grid_width)
            block_width = max(2, int(block.duration / seconds * grid_width) - 3)
            selected = self._block_selected(channel, block)
            if block.is_standby:
                color = STANDBY_BLOCK
            elif selected:
                color = BLOCK_SELECTED
            elif index % 2 == 0:
                color = BLOCK
            else:
                color = BLOCK_ALT
            rect_top = top + 6
            rect_height = row_height - 14
            painter.fillRect(x, rect_top, block_width, rect_height, color)
            if selected:
                painter.setPen(QPen(GOLD, 3))
                painter.drawRect(x, rect_top, block_width, rect_height)
            if block_width > 48:
                painter.setPen(TEXT)
                painter.setFont(_font(15, bold=selected))
                title = QFontMetrics(painter.font()).elidedText(
                    block.title, Qt.TextElideMode.ElideRight, block_width - 16
                )
                painter.drawText(x + 8, rect_top, block_width - 16, rect_height, Qt.AlignmentFlag.AlignVCenter, title)

    def _draw_now_line(
        self,
        painter: QPainter,
        grid_top: int,
        grid_bottom: int,
        grid_width: int,
        visible: timedelta,
    ) -> None:
        window_end = self.origin + visible
        if not (self.origin <= self.now < window_end):
            return
        x = LEFT_WIDTH + int((self.now - self.origin).total_seconds() / visible.total_seconds() * grid_width)
        painter.setPen(QPen(NOW, 2))
        painter.drawLine(x, grid_top, x, grid_bottom)

    def _draw_footer(self, painter: QPainter, top: int, width: int) -> None:
        channel = self._selected_channel()
        painter.setPen(TEXT)
        if channel is None or self.selected_start is None:
            painter.setFont(_font(18))
            painter.drawText(24, top, width - 48, FOOTER_HEIGHT, Qt.AlignmentFlag.AlignVCenter, "No program selected.")
            return
        airing = now_playing(channel, self.selected_start, self.videos)
        when = "On now" if airing.start <= self.now < airing.end else ("Earlier" if airing.end <= self.now else "Later")
        painter.setFont(_font(16))
        painter.setPen(GOLD)
        painter.drawText(24, top + 12, width - 48, 28, Qt.AlignmentFlag.AlignVCenter, f"{channel.number}  {channel.name}   ·   {when}")
        painter.setPen(TEXT)
        painter.setFont(_font(22, bold=True))
        painter.drawText(24, top + 40, width - 48, 32, Qt.AlignmentFlag.AlignVCenter, airing.title)
        painter.setFont(_font(16))
        painter.setPen(MUTED)
        painter.drawText(24, top + 72, width - 48, 28, Qt.AlignmentFlag.AlignVCenter, format_range(airing.start, airing.end))

    def _row_geometry(self, grid_height: int) -> tuple[int, int, int]:
        count = len(self.channels)
        if count == 0:
            return ROW_MIN, 0, 0
        fitted = grid_height / count
        if fitted >= ROW_MIN:
            return int(fitted), 0, count
        visible_rows = max(1, grid_height // ROW_MIN)
        first = min(self._row_offset, max(0, count - visible_rows))
        self._row_offset = first
        return ROW_MIN, first, min(count, first + visible_rows)

    def _reveal_row(self) -> None:
        if self.selected_number is None:
            return
        numbers = [channel.number for channel in self.channels]
        if self.selected_number not in numbers:
            return
        index = numbers.index(self.selected_number)
        visible = self._visible_row_count()
        if index < self._row_offset:
            self._row_offset = index
        elif index >= self._row_offset + visible:
            self._row_offset = index - visible + 1

    def _visible_row_count(self) -> int:
        grid_height = self.height() - HEADER_HEIGHT - FOOTER_HEIGHT
        count = len(self.channels)
        if count == 0 or grid_height <= 0:
            return 1
        if grid_height / count >= ROW_MIN:
            return count
        return max(1, grid_height // ROW_MIN)

    def _reveal_time(self, start: datetime) -> None:
        visible = timedelta(hours=self.guide_hours)
        if start < self.origin or start >= self.origin + visible - timedelta(minutes=5):
            self.origin = floor_half_hour(start)

    def _selected_channel(self) -> Channel | None:
        if self.selected_number is None:
            return None
        return find_channel(self.channels, self.selected_number)

    def _block_selected(self, channel: Channel, block: Airing) -> bool:
        if channel.number != self.selected_number or self.selected_start is None:
            return False
        return abs((self._true_start(channel, block) - self.selected_start).total_seconds()) < 1

    def _true_start(self, channel: Channel, block: Airing) -> datetime:
        return now_playing(channel, block.start, self.videos).start


def _font(size: int, bold: bool = False) -> QFont:
    font = QFont()
    font.setFamilies(["Segoe UI", "DejaVu Sans", "Liberation Sans", "Sans Serif"])
    font.setPixelSize(size)
    font.setBold(bold)
    return font


def _closest_index(moments: list[datetime], target: datetime) -> int:
    best = 0
    best_gap = abs((moments[0] - target).total_seconds())
    for index, moment in enumerate(moments[1:], start=1):
        gap = abs((moment - target).total_seconds())
        if gap < best_gap:
            best = index
            best_gap = gap
    return best
