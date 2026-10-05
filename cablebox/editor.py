"""Editor for the video collection and the channel rules that play it.

Tag videos once. A channel block names a time and the tags to play, and the
schedule fills that time from the collection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QTime, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from cablebox.format import format_day_offset, format_duration, format_range
from cablebox.library import Library, ProbeError, probe_duration, save_library
from cablebox.schedule import (
    DAY_SECONDS,
    ORDER_SERIES,
    ORDER_SHUFFLE,
    Block,
    Channel,
    Video,
    guide_blocks,
    midnight_of,
    parse_tag_text,
    play_order_key,
    videos_for_block,
)

VIDEO_FILTER = "Videos (*.mp4 *.mkv *.avi *.mov *.m4v *.webm *.wmv *.mpg *.mpeg *.ts *.m2ts);;All files (*.*)"

STYLE = """
QWidget#EditorPage { background: #07111f; color: #f2f6fa; }
QLabel { color: #f2f6fa; }
QListWidget {
    background: #0c1b2e;
    color: #f2f6fa;
    border: 1px solid #1e3a5f;
    padding: 4px;
    font-size: 15px;
}
QListWidget::item { padding: 8px 6px; }
QListWidget::item:selected { background: #1a4a7a; color: #f2f6fa; }
QLineEdit, QSpinBox, QTimeEdit, QComboBox {
    background: #0c1b2e;
    color: #f2f6fa;
    border: 1px solid #1e3a5f;
    padding: 6px;
    min-height: 28px;
    font-size: 16px;
}
QPushButton {
    background: #163e68;
    color: #f2f6fa;
    border: 1px solid #2a6eaa;
    padding: 8px 14px;
    font-size: 15px;
}
QPushButton:hover { background: #1f568c; }
QPushButton:disabled { background: #122033; color: #6e8196; border-color: #1e3a5f; }
"""


@dataclass
class DraftVideo:
    path: str
    title: str
    duration: float
    tags: list[str] = field(default_factory=list)
    series: str | None = None
    episode: int | None = None


@dataclass
class DraftBlock:
    start: int
    end: int
    tags: list[str] = field(default_factory=list)
    order: str = ORDER_SERIES


@dataclass
class DraftChannel:
    number: int
    name: str
    blocks: list[DraftBlock] = field(default_factory=list)


class EditorPage(QWidget):
    saved = Signal(object)
    closed = Signal()

    def __init__(self, path: Path, parent=None) -> None:
        super().__init__(parent)
        self.path = path
        self.setObjectName("EditorPage")
        self.setStyleSheet(STYLE)
        self._videos: list[DraftVideo] = []
        self._channels: list[DraftChannel] = []
        self._last_channel: int | None = None
        self._guide_hours = 2.0
        self._volume = 70
        self._dirty = False
        self._loading = False
        self._build()

    def load_from(self, library: Library) -> None:
        self._last_channel = library.last_channel
        self._guide_hours = library.guide_hours
        self._volume = library.volume
        self._videos = [
            DraftVideo(video.path, video.title, video.duration, list(video.tags), video.series, video.episode)
            for video in library.videos
        ]
        self._channels = [
            DraftChannel(
                channel.number,
                channel.name,
                [DraftBlock(block.start, block.end, list(block.tags), block.order) for block in channel.blocks],
            )
            for channel in library.channels
        ]
        self._dirty = False
        self._status.setText("Tag the collection, then add time blocks. The channel fills those times itself.")
        self._refresh_videos()
        self._refresh_channels(self._channels[0] if self._channels else None)

    def note_volume(self, level: int) -> None:
        self._volume = level

    def note_last_channel(self, number: int | None) -> None:
        self._last_channel = number

    def focus_default(self) -> None:
        self._video_tags.setFocus()

    def confirm_close(self) -> bool:
        if not self._dirty:
            return True
        answer = QMessageBox.question(
            self,
            "Save lineup?",
            "The lineup has unsaved changes.",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Save:
            return self._save()
        self._dirty = False
        return True

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)

        title = QLabel("Lineup")
        title.setStyleSheet("font-size: 28px; font-weight: 700;")
        root.addWidget(title)
        intro = QLabel(
            "Tag videos once, and set a series name such as naruto plus an episode when a show has to stay in order. "
            "A block such as naruto from 8:00 PM to 10:00 PM plays those episodes for you "
            "and continues the next day. Shuffle, as in 00s, anime at 10:30, builds a new order each day."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #c5d4e8; font-size: 15px;")
        root.addWidget(intro)

        splitter = QSplitter()
        splitter.addWidget(self._collection_panel())
        splitter.addWidget(self._channel_panel())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, stretch=1)

        actions = QHBoxLayout()
        save_button = QPushButton("Save")
        save_button.clicked.connect(lambda: self._save())
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.closed.emit)
        actions.addWidget(save_button)
        actions.addWidget(close_button)
        actions.addStretch(1)
        self._status = QLabel("")
        self._status.setStyleSheet("color: #c5d4e8;")
        actions.addWidget(self._status)
        root.addLayout(actions)

    def _collection_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.addWidget(QLabel("Collection"))
        self._video_list = QListWidget()
        self._video_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self._video_list.itemSelectionChanged.connect(self._on_video_selection)
        layout.addWidget(self._video_list, stretch=1)

        self._title = QLineEdit()
        self._title.setPlaceholderText("Title")
        self._title.editingFinished.connect(self._on_title_edited)
        layout.addWidget(self._title)

        numbers = QHBoxLayout()
        numbers.addWidget(QLabel("Series"))
        self._series = QLineEdit()
        self._series.setPlaceholderText("naruto")
        self._series.textEdited.connect(self._on_numbers_changed)
        self._series.editingFinished.connect(self._on_numbers_finished)
        numbers.addWidget(self._series, stretch=1)
        numbers.addWidget(QLabel("Episode"))
        self._episode = QSpinBox()
        self._episode.setRange(-1, 9999)
        self._episode.setSpecialValueText("—")
        self._episode.setValue(-1)
        self._episode.valueChanged.connect(self._on_numbers_changed)
        self._episode.editingFinished.connect(self._on_numbers_finished)
        numbers.addWidget(self._episode)
        number_button = QPushButton("Number in order")
        number_button.clicked.connect(self._number_in_order)
        numbers.addWidget(number_button)
        numbers.addStretch(1)
        layout.addLayout(numbers)

        self._video_tags = QLineEdit()
        self._video_tags.setPlaceholderText("Tags, separated by commas: naruto, anime, shounen, action, 00s")
        layout.addWidget(self._video_tags)

        buttons = QHBoxLayout()
        add = QPushButton("Add videos")
        add.clicked.connect(self._add_videos)
        apply_tags = QPushButton("Apply tags")
        apply_tags.clicked.connect(self._apply_tags)
        remove = QPushButton("Remove")
        remove.clicked.connect(self._remove_videos)
        for button in (add, apply_tags, remove):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        return panel

    def _channel_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 0, 0, 0)
        layout.addWidget(QLabel("Channels"))
        self._channel_list = QListWidget()
        self._channel_list.currentRowChanged.connect(self._on_channel_row)
        layout.addWidget(self._channel_list)

        channel_buttons = QHBoxLayout()
        add_channel = QPushButton("Add channel")
        add_channel.clicked.connect(self._add_channel)
        remove_channel = QPushButton("Remove")
        remove_channel.clicked.connect(self._remove_channel)
        channel_buttons.addWidget(add_channel)
        channel_buttons.addWidget(remove_channel)
        layout.addLayout(channel_buttons)

        fields = QHBoxLayout()
        fields.addWidget(QLabel("Number"))
        self._number = QSpinBox()
        self._number.setRange(1, 9999)
        self._number.valueChanged.connect(self._on_number_changed)
        fields.addWidget(self._number)
        fields.addWidget(QLabel("Name"))
        self._name = QLineEdit()
        self._name.textEdited.connect(self._on_name_edited)
        fields.addWidget(self._name, stretch=1)
        layout.addLayout(fields)

        layout.addWidget(QLabel("Blocks"))
        self._block_list = QListWidget()
        self._block_list.currentRowChanged.connect(self._on_block_row)
        layout.addWidget(self._block_list, stretch=1)

        times = QHBoxLayout()
        times.addWidget(QLabel("From"))
        self._start = QTimeEdit()
        self._start.setDisplayFormat("h:mm AP")
        self._start.setTime(QTime(20, 0))
        self._start.timeChanged.connect(self._on_block_edited)
        times.addWidget(self._start)
        times.addWidget(QLabel("to"))
        self._end = QTimeEdit()
        self._end.setDisplayFormat("h:mm AP")
        self._end.setTime(QTime(22, 0))
        self._end.timeChanged.connect(self._on_block_edited)
        times.addWidget(self._end)
        self._order = QComboBox()
        self._order.addItem("In order", ORDER_SERIES)
        self._order.addItem("Shuffle", ORDER_SHUFFLE)
        self._order.currentIndexChanged.connect(self._on_block_edited)
        times.addWidget(self._order)
        layout.addLayout(times)

        self._block_tags = QLineEdit()
        self._block_tags.setPlaceholderText("Play videos tagged: naruto    or    00s, anime")
        self._block_tags.editingFinished.connect(self._on_block_edited)
        layout.addWidget(self._block_tags)

        block_buttons = QHBoxLayout()
        add_block = QPushButton("Add block")
        add_block.clicked.connect(self._add_block)
        remove_block = QPushButton("Remove block")
        remove_block.clicked.connect(self._remove_block)
        block_buttons.addWidget(add_block)
        block_buttons.addWidget(remove_block)
        block_buttons.addStretch(1)
        layout.addLayout(block_buttons)

        self._preview = QLabel("Add a block and it fills itself from the tags.")
        self._preview.setWordWrap(True)
        self._preview.setStyleSheet("color: #c5d4e8;")
        layout.addWidget(self._preview)
        self._set_channel_fields_enabled(False)
        return panel

    def _refresh_videos(self, select_path: str | None = None, select_paths: set[str] | None = None) -> None:
        self._videos.sort(key=_draft_order)
        wanted = set(select_paths or ())
        if select_path is not None:
            wanted.add(select_path)
        self._loading = True
        self._video_list.clear()
        selected_row = 0
        for index, video in enumerate(self._videos):
            item = QListWidgetItem(_video_label(video))
            item.setData(Qt.ItemDataRole.UserRole, video.path)
            self._video_list.addItem(item)
            if video.path in wanted:
                selected_row = index
                item.setSelected(True)
        self._loading = False
        if len(wanted) == 1:
            self._video_list.setCurrentRow(selected_row)
        elif not self._videos:
            self._title.clear()
            self._title.setEnabled(False)

    def _selected_videos(self) -> list[DraftVideo]:
        paths = {item.data(Qt.ItemDataRole.UserRole) for item in self._video_list.selectedItems()}
        return [video for video in self._videos if video.path in paths]

    def _on_video_selection(self) -> None:
        if self._loading:
            return
        selected = self._selected_videos()
        self._title.setEnabled(len(selected) == 1)
        if len(selected) == 1:
            self._loading = True
            self._title.setText(selected[0].title)
            self._series.setText("" if selected[0].series is None else selected[0].series)
            self._episode.setValue(-1 if selected[0].episode is None else selected[0].episode)
            self._video_tags.setText(", ".join(selected[0].tags))
            self._loading = False

    def _on_title_edited(self) -> None:
        if self._loading:
            return
        selected = self._selected_videos()
        if len(selected) != 1:
            return
        title = self._title.text().strip() or Path(selected[0].path).stem
        if title == selected[0].title:
            return
        selected[0].title = title
        self._dirty = True
        self._refresh_videos(selected[0].path)
        self._refresh_preview()

    def _on_numbers_changed(self, *_args: object) -> None:
        if self._loading:
            return
        selected = self._selected_videos()
        if len(selected) != 1:
            return
        video = selected[0]
        series = _series_name(self._series)
        episode = _spin_count(self._episode)
        if video.series == series and video.episode == episode:
            return
        video.series = series
        video.episode = episode
        self._dirty = True
        self._rewrite_video_row(video)
        self._refresh_preview()

    def _on_numbers_finished(self) -> None:
        if self._loading:
            return
        selected = self._selected_videos()
        if len(selected) != 1:
            return
        self._refresh_videos(selected[0].path)
        self._refresh_preview()

    def _number_in_order(self) -> None:
        selected = self._selected_videos()
        if not selected:
            self._status.setText("Select the episodes, type the series name and the first episode, then number them.")
            return
        series = _series_name(self._series)
        episode = _spin_count(self._episode)
        ordered = [video for video in self._videos if video in selected]
        if series is None and episode is None:
            if len(ordered) == 1:
                ordered[0].series = None
                ordered[0].episode = None
                self._dirty = True
                self._refresh_videos(ordered[0].path)
                self._refresh_preview()
                self._status.setText("Cleared the series and episode.")
            else:
                self._status.setText("Type a series name, an episode number, or both.")
            return
        if len(ordered) == 1 or episode is None:
            for video in ordered:
                if series is not None:
                    video.series = series
                if episode is not None:
                    video.episode = episode
            detail = "Set the number on 1 video." if len(ordered) == 1 else f"Set series {series} on {len(ordered)} videos."
        else:
            for offset, video in enumerate(ordered):
                if series is not None:
                    video.series = series
                video.episode = episode + offset
            last = episode + len(ordered) - 1
            series_text = f"S{series} " if series is not None else ""
            detail = f"Numbered {len(ordered)} videos {series_text}E{episode} through E{last}."
        self._dirty = True
        self._refresh_videos(select_paths={video.path for video in ordered})
        self._refresh_preview()
        self._status.setText(detail)

    def _rewrite_video_row(self, video: DraftVideo) -> None:
        for row in range(self._video_list.count()):
            item = self._video_list.item(row)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == video.path:
                item.setText(_video_label(video))
                return

    def _apply_tags(self) -> None:
        selected = self._selected_videos()
        tags = list(parse_tag_text(self._video_tags.text()))
        if not selected:
            self._status.setText("Select videos, then apply tags.")
            return
        if not tags and len(selected) != 1:
            self._status.setText("Type the tags to apply, separated by commas.")
            return
        if len(selected) == 1:
            selected[0].tags = tags
        else:
            for video in selected:
                for tag in tags:
                    if tag not in video.tags:
                        video.tags.append(tag)
        self._dirty = True
        self._refresh_videos(selected[0].path)
        self._refresh_preview()
        self._status.setText(f"Tagged {len(selected)} video{'s' if len(selected) != 1 else ''}.")

    def _add_videos(self) -> None:
        paths, _selected_filter = QFileDialog.getOpenFileNames(self, "Add videos", "", VIDEO_FILTER)
        if not paths:
            return
        tags = list(parse_tag_text(self._video_tags.text()))
        added = 0
        last_path = None
        known = {video.path: video for video in self._videos}
        for raw in paths:
            path = Path(raw)
            try:
                QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                try:
                    duration = probe_duration(path)
                finally:
                    QApplication.restoreOverrideCursor()
            except ProbeError as exc:
                QMessageBox.warning(self, "Could not add video", str(exc))
                continue
            existing = known.get(str(path))
            if existing is None:
                video = DraftVideo(str(path), path.stem, duration, list(tags))
                self._videos.append(video)
                known[video.path] = video
            else:
                existing.duration = duration
                for tag in tags:
                    if tag not in existing.tags:
                        existing.tags.append(tag)
            last_path = str(path)
            added += 1
        if added:
            self._dirty = True
            self._refresh_videos(last_path)
            self._refresh_preview()
            self._status.setText(f"Added {added} video{'s' if added != 1 else ''}.")

    def _remove_videos(self) -> None:
        selected = self._selected_videos()
        if not selected:
            return
        paths = {video.path for video in selected}
        self._videos = [video for video in self._videos if video.path not in paths]
        self._dirty = True
        self._refresh_videos()
        self._refresh_preview()

    def _refresh_channels(self, keep: DraftChannel | None) -> None:
        self._channels.sort(key=lambda channel: (channel.number, channel.name.casefold()))
        self._loading = True
        self._channel_list.clear()
        selected_row = 0
        for index, channel in enumerate(self._channels):
            item = QListWidgetItem(f"{channel.number}    {channel.name}")
            self._channel_list.addItem(item)
            if keep is not None and channel is keep:
                selected_row = index
        self._loading = False
        if self._channels:
            self._channel_list.setCurrentRow(selected_row)
        else:
            self._block_list.clear()
            self._preview.setText("Add a channel, then add a block for a tag and a time.")
            self._set_channel_fields_enabled(False)

    def _selected_channel(self) -> DraftChannel | None:
        row = self._channel_list.currentRow()
        if row < 0 or row >= len(self._channels):
            return None
        return self._channels[row]

    def _set_channel_fields_enabled(self, enabled: bool) -> None:
        self._number.setEnabled(enabled)
        self._name.setEnabled(enabled)
        self._start.setEnabled(enabled)
        self._end.setEnabled(enabled)
        self._order.setEnabled(enabled)
        self._block_tags.setEnabled(enabled)

    def _on_channel_row(self, row: int) -> None:
        if self._loading:
            return
        if row < 0 or row >= len(self._channels):
            self._set_channel_fields_enabled(False)
            return
        channel = self._channels[row]
        self._loading = True
        self._number.setValue(channel.number)
        self._name.setText(channel.name)
        self._loading = False
        self._set_channel_fields_enabled(True)
        self._refresh_blocks(channel.blocks[0] if channel.blocks else None)

    def _on_number_changed(self, value: int) -> None:
        if self._loading:
            return
        channel = self._selected_channel()
        if channel is None or channel.number == value:
            return
        channel.number = value
        self._dirty = True
        self._refresh_channels(channel)

    def _on_name_edited(self, text: str) -> None:
        if self._loading:
            return
        channel = self._selected_channel()
        if channel is None:
            return
        channel.name = text
        self._dirty = True
        item = self._channel_list.currentItem()
        if item is not None:
            item.setText(f"{channel.number}    {channel.name}")

    def _add_channel(self) -> None:
        numbers = {channel.number for channel in self._channels}
        number = 2
        while number in numbers:
            number += 1
        channel = DraftChannel(number, f"Channel {number}")
        self._channels.append(channel)
        self._dirty = True
        self._refresh_channels(channel)
        self._status.setText("Channel added. Add a block for what it should play.")

    def _remove_channel(self) -> None:
        channel = self._selected_channel()
        if channel is None:
            return
        answer = QMessageBox.question(
            self,
            "Remove channel?",
            f"Remove channel {channel.number} ({channel.name})?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._channels.remove(channel)
        self._dirty = True
        self._refresh_channels(self._channels[0] if self._channels else None)

    def _refresh_blocks(self, keep: DraftBlock | None) -> None:
        channel = self._selected_channel()
        self._loading = True
        self._block_list.clear()
        if channel is None:
            self._loading = False
            return
        channel.blocks.sort(key=lambda block: block.start)
        selected_row = 0
        for index, block in enumerate(channel.blocks):
            label = _block_label(block)
            self._block_list.addItem(label)
            if keep is not None and block is keep:
                selected_row = index
        self._loading = False
        if channel.blocks:
            self._block_list.setCurrentRow(selected_row)
        else:
            self._preview.setText("Add a block. It plays every video that has all of its tags.")

    def _selected_block(self) -> DraftBlock | None:
        channel = self._selected_channel()
        row = self._block_list.currentRow()
        if channel is None or row < 0 or row >= len(channel.blocks):
            return None
        return channel.blocks[row]

    def _on_block_row(self, row: int) -> None:
        if self._loading:
            return
        block = self._selected_block()
        if block is None:
            self._preview.setText("Add a block. It plays every video that has all of its tags.")
            return
        self._load_block_fields(block)
        self._refresh_preview()

    def _load_block_fields(self, block: DraftBlock) -> None:
        self._loading = True
        self._start.setTime(_qtime_from_seconds(block.start))
        self._end.setTime(_qtime_from_seconds(block.end if block.end < DAY_SECONDS else 0))
        self._block_tags.setText(", ".join(block.tags))
        order_index = self._order.findData(block.order)
        self._order.setCurrentIndex(order_index if order_index >= 0 else 0)
        self._loading = False

    def _on_block_edited(self, *_args: object) -> None:
        if self._loading:
            return
        channel = self._selected_channel()
        block = self._selected_block()
        if channel is None or block is None:
            return
        start = _qtime_to_start(self._start.time())
        end = _qtime_to_end(self._end.time())
        tags = list(parse_tag_text(self._block_tags.text()))
        order = str(self._order.currentData() or ORDER_SERIES)
        if end <= start:
            self._status.setText("The block has to end after it starts. 12:00 AM means midnight.")
            self._load_block_fields(block)
            return
        others = [item for item in channel.blocks if item is not block]
        if _overlaps(start, end, others):
            self._status.setText("That time overlaps another block on this channel.")
            self._load_block_fields(block)
            return
        if not tags:
            self._status.setText("A block needs at least one tag.")
            self._load_block_fields(block)
            return
        block.start = start
        block.end = end
        block.tags = tags
        block.order = order
        self._dirty = True
        self._refresh_blocks(block)
        self._refresh_preview()

    def _add_block(self) -> None:
        channel = self._selected_channel()
        if channel is None:
            QMessageBox.information(self, "Add a channel first", "Create a channel, then add a block to it.")
            return
        selected = self._selected_block()
        if selected is None:
            start = _qtime_to_start(self._start.time())
            end = _qtime_to_end(self._end.time())
            tags = list(parse_tag_text(self._block_tags.text())) or list(parse_tag_text(self._video_tags.text()))
            order = str(self._order.currentData() or ORDER_SERIES)
            if not tags:
                self._status.setText("Type the tags this block should play.")
                self._block_tags.setFocus()
                return
            if end <= start:
                self._status.setText("The block has to end after it starts. 12:00 AM means midnight.")
                return
            if _overlaps(start, end, channel.blocks):
                self._status.setText("That time overlaps another block on this channel.")
                return
        else:
            slot = _next_slot(channel.blocks)
            if slot is None:
                self._status.setText("This channel already has something scheduled all day.")
                return
            start, end = slot
            tags = list(selected.tags)
            order = ORDER_SERIES
        block = DraftBlock(start, end, tags, order)
        channel.blocks.append(block)
        self._dirty = True
        self._refresh_blocks(block)
        self._status.setText("Block added. Change its tags or time, then save.")

    def _remove_block(self) -> None:
        channel = self._selected_channel()
        block = self._selected_block()
        if channel is None or block is None:
            return
        channel.blocks.remove(block)
        self._dirty = True
        self._refresh_blocks(channel.blocks[0] if channel.blocks else None)

    def _refresh_preview(self) -> None:
        channel = self._selected_channel()
        block = self._selected_block()
        if channel is None or block is None:
            return
        videos = _as_videos(self._videos)
        scheduled = _as_channel(channel)
        rule = _as_block(block)
        matched = videos_for_block(videos, rule, datetime.now(), channel.number)
        if not block.tags:
            self._preview.setText("Add tags. The block plays videos that have every tag.")
            return
        if not matched:
            self._preview.setText("No videos have all of these tags yet.")
            return
        if block.order == ORDER_SHUFFLE:
            how = "shuffled once each day"
        else:
            how = "in order, continuing the next day"
        lines = [f"{len(matched)} videos, {how}."]
        day = midnight_of(datetime.now())
        window_start = day + timedelta(seconds=block.start)
        window_end = day + timedelta(seconds=block.end)
        airings = [airing for airing in guide_blocks(scheduled, window_start, window_end, videos) if not airing.is_standby]
        for airing in airings[:8]:
            lines.append(f"{format_range(airing.start, airing.end)}   {airing.title}")
        if len(airings) > 8:
            lines.append(f"and {len(airings) - 8} more in this block")
        self._preview.setText("\n".join(lines))

    def _save(self) -> bool:
        numbers = [channel.number for channel in self._channels]
        if len(numbers) != len(set(numbers)):
            QMessageBox.warning(self, "Channel numbers", "Each channel needs its own number.")
            return False
        for channel in self._channels:
            if _has_overlap(channel.blocks):
                QMessageBox.warning(self, "Overlapping blocks", f"Channel {channel.number} has overlapping times.")
                return False
        library = Library(
            channels=[_as_channel(channel) for channel in sorted(self._channels, key=lambda item: item.number)],
            last_channel=self._last_channel,
            guide_hours=self._guide_hours,
            videos=_as_videos(self._videos),
            volume=self._volume,
        )
        try:
            save_library(self.path, library)
        except OSError as exc:
            QMessageBox.critical(self, "Could not save", str(exc))
            return False
        self._dirty = False
        self._status.setText("Saved.")
        self.saved.emit(library)
        return True


def _draft_order(video: DraftVideo) -> tuple:
    return play_order_key(Video(video.path, video.title, video.duration, tuple(video.tags), video.series, video.episode))


def _video_label(video: DraftVideo) -> str:
    tags = ", ".join(video.tags) if video.tags else "no tags"
    length = format_duration(video.duration) if video.duration > 0 else "—"
    numbers = _number_label(video)
    prefix = f"{numbers}    " if numbers else ""
    return f"{prefix}{video.title}    {length}    {tags}"


def _number_label(video: DraftVideo) -> str:
    parts: list[str] = []
    if video.series:
        parts.append(f"S{video.series}" if video.series.isdigit() else video.series)
    if video.episode is not None:
        parts.append(f"E{video.episode}")
    return " ".join(parts)


def _series_name(box) -> str | None:
    text = box.text().strip()
    return text or None


def _spin_count(box) -> int | None:
    value = box.value()
    if value < 0:
        return None
    return int(value)


def _as_videos(drafts: list[DraftVideo]) -> list[Video]:
    return [
        Video(
            video.path,
            video.title.strip() or Path(video.path).stem,
            video.duration,
            tuple(video.tags),
            video.series,
            video.episode,
        )
        for video in drafts
    ]


def _as_block(draft: DraftBlock) -> Block:
    return Block(draft.start, draft.end, tuple(draft.tags), draft.order)


def _as_channel(draft: DraftChannel) -> Channel:
    name = draft.name.strip() or f"Channel {draft.number}"
    blocks = tuple(_as_block(block) for block in sorted(draft.blocks, key=lambda item: item.start))
    return Channel(draft.number, name, blocks)


def _block_label(block: DraftBlock) -> str:
    tags = ", ".join(block.tags) if block.tags else "no tags"
    order = "Shuffle" if block.order == ORDER_SHUFFLE else "In order"
    return f"{format_day_offset(block.start)} – {format_day_offset(block.end)}    {tags}    {order}"


def _qtime_from_seconds(seconds: int) -> QTime:
    seconds = seconds % DAY_SECONDS
    hours, remainder = divmod(int(seconds), 3600)
    return QTime(hours, remainder // 60)


def _qtime_to_start(value: QTime) -> int:
    return value.hour() * 3600 + value.minute() * 60


def _qtime_to_end(value: QTime) -> int:
    seconds = value.hour() * 3600 + value.minute() * 60
    if seconds == 0:
        return DAY_SECONDS
    return seconds


def _overlaps(start: int, end: int, blocks: list[DraftBlock]) -> bool:
    return any(start < block.end and end > block.start for block in blocks)


def _next_slot(blocks: list[DraftBlock]) -> tuple[int, int] | None:
    """The next free stretch: after the latest block, or the earliest gap."""
    if not blocks:
        return 20 * 3600, 22 * 3600
    latest_end = max(block.end for block in blocks)
    if DAY_SECONDS - latest_end >= 30 * 60:
        return latest_end, min(latest_end + 2 * 3600, DAY_SECONDS)
    cursor = 0
    for block in sorted(blocks, key=lambda item: item.start):
        if block.start - cursor >= 30 * 60:
            return cursor, min(cursor + 2 * 3600, block.start)
        cursor = max(cursor, block.end)
    return None


def _has_overlap(blocks: list[DraftBlock]) -> bool:
    ordered = sorted(blocks, key=lambda block: block.start)
    return any(previous.end > current.start for previous, current in zip(ordered, ordered[1:]))
