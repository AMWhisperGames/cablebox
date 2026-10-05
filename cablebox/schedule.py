"""Clock-synced channel schedule.

Videos live in one collection, each with tags. A channel does not list every
episode. It lists blocks: a time range, the tags to play, and whether to play
them in order or shuffled. Tuning in joins the current video already in progress.
"""

from __future__ import annotations

import random
import re
import zlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

DAY_SECONDS = 24 * 60 * 60
ORDER_SERIES = "series"
ORDER_SHUFFLE = "shuffle"
ANCHOR = date(2020, 1, 1)

_NUMBER_SPLIT = re.compile(r"(\d+)")


@dataclass(frozen=True)
class Video:
    path: str
    title: str
    duration: float
    tags: tuple[str, ...] = ()
    series: str | None = None
    episode: int | None = None


@dataclass(frozen=True)
class Block:
    """A rule that fills a stretch of the day from tagged videos."""

    start: int
    end: int
    tags: tuple[str, ...]
    order: str = ORDER_SERIES


@dataclass(frozen=True)
class Channel:
    number: int
    name: str
    blocks: tuple[Block, ...] = ()


@dataclass(frozen=True)
class Airing:
    """What a channel is broadcasting at a moment."""

    channel_number: int
    title: str
    path: str | None
    start: datetime
    end: datetime
    offset: float
    is_standby: bool

    @property
    def duration(self) -> float:
        return max(0.0, (self.end - self.start).total_seconds())


def normalize_tags(tags: Iterable[str]) -> tuple[str, ...]:
    cleaned: list[str] = []
    for tag in tags:
        text = str(tag).strip().casefold()
        if text and text not in cleaned:
            cleaned.append(text)
    return tuple(cleaned)


def parse_tag_text(text: str) -> tuple[str, ...]:
    return normalize_tags(re.split(r"[,;\n]+", text))


def seconds_since_midnight(moment: datetime) -> float:
    midnight = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    return (moment - midnight).total_seconds()


def midnight_of(moment: datetime) -> datetime:
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


def next_midnight(moment: datetime) -> datetime:
    return midnight_of(moment) + timedelta(days=1)


def floor_half_hour(moment: datetime) -> datetime:
    minute = 0 if moment.minute < 30 else 30
    return moment.replace(minute=minute, second=0, microsecond=0)


def sorted_channels(channels: list[Channel]) -> list[Channel]:
    return sorted(channels, key=lambda channel: (channel.number, channel.name.casefold()))


def find_channel(channels: list[Channel], number: int) -> Channel | None:
    for channel in channels:
        if channel.number == number:
            return channel
    return None


def step_channel(channels: list[Channel], current: int | None, direction: int) -> Channel | None:
    ordered = sorted_channels(channels)
    if not ordered:
        return None
    if current is None:
        return ordered[0]
    numbers = [channel.number for channel in ordered]
    if current not in numbers:
        return ordered[0]
    index = numbers.index(current)
    return ordered[(index + direction) % len(ordered)]


def videos_for_block(
    videos: Sequence[Video],
    block: Block,
    moment: datetime,
    channel_number: int,
) -> tuple[Video, ...]:
    """Videos a block will play, in broadcast order."""
    matched = [video for video in videos if _matches(video, block.tags)]
    if not matched:
        return ()
    if block.order == ORDER_SHUFFLE:
        return _shuffled(matched, channel_number, block, moment)
    return tuple(sorted(matched, key=play_order_key))


def now_playing(channel: Channel, moment: datetime, videos: Sequence[Video] = ()) -> Airing:
    seconds = seconds_since_midnight(moment)
    block = _block_at(channel, seconds)
    if block is None:
        return _gap(channel, moment, seconds)

    playlist = videos_for_block(videos, block, moment, channel.number)
    block_start = _at_seconds(moment, block.start)
    block_end = _at_seconds(moment, block.end)
    if not playlist:
        return _slate(channel, block_start, block_end, "No matching videos")

    cycle = sum(video.duration for video in playlist)
    if block.order == ORDER_SHUFFLE:
        position = (seconds - block.start) % cycle
    else:
        aired = _days_since_anchor(moment) * (block.end - block.start) + (seconds - block.start)
        position = aired % cycle
    video, offset = _video_at(playlist, position)
    remaining = max(0.0, video.duration - offset)
    natural_end = moment + timedelta(seconds=remaining)
    end = block_end if natural_end >= block_end else natural_end
    if end <= moment:
        end = moment + timedelta(milliseconds=1)
    episode_start = moment - timedelta(seconds=offset)
    start = episode_start if episode_start >= block_start else block_start
    return Airing(
        channel_number=channel.number,
        title=_air_title(video),
        path=video.path,
        start=start,
        end=end,
        offset=offset,
        is_standby=False,
    )


def guide_blocks(
    channel: Channel,
    window_start: datetime,
    window_end: datetime,
    videos: Sequence[Video] = (),
) -> list[Airing]:
    """Airings covering ``[window_start, window_end)`` with no gaps or overlap."""
    if window_end <= window_start:
        return []

    blocks: list[Airing] = []
    cursor = window_start
    for _ in range(10000):
        if cursor >= window_end:
            break
        airing = now_playing(channel, cursor, videos)
        block_end = airing.end if airing.end < window_end else window_end
        if block_end <= cursor:
            cursor = cursor + timedelta(milliseconds=1)
            continue
        blocks.append(
            Airing(
                channel_number=airing.channel_number,
                title=airing.title,
                path=airing.path,
                start=cursor,
                end=block_end,
                offset=airing.offset,
                is_standby=airing.is_standby,
            )
        )
        cursor = block_end
    return blocks


def _matches(video: Video, tags: tuple[str, ...]) -> bool:
    if video.duration <= 0 or not tags:
        return False
    have = set(video.tags)
    return all(tag in have for tag in tags)


def play_order_key(video: Video) -> tuple:
    """Named series first, in name order, then by episode, then by title."""
    if not video.series and video.episode is None:
        return (1, (), 0, _natural_key(video))
    episode = 0 if video.episode is None else video.episode
    return (0, _text_key(video.series or ""), episode, _natural_key(video))


def _air_title(video: Video) -> str:
    parts: list[str] = []
    if video.series:
        parts.append(f"S{video.series}" if video.series.isdigit() else video.series)
    if video.episode is not None:
        parts.append(f"E{video.episode}")
    if not parts:
        return video.title
    return f"{' '.join(parts)}  {video.title}"


def _text_key(text: str) -> tuple:
    pieces: list[tuple] = []
    for part in _NUMBER_SPLIT.split(text):
        if part.isdigit():
            pieces.append((0, int(part)))
        elif part:
            pieces.append((1, part.casefold()))
    return tuple(pieces)


def _natural_key(video: Video) -> tuple:
    pieces: list[tuple] = []
    for part in _NUMBER_SPLIT.split(video.title):
        if part.isdigit():
            pieces.append((0, int(part)))
        elif part:
            pieces.append((1, part.casefold()))
    pieces.append((2, video.path.casefold()))
    return tuple(pieces)


def _shuffled(videos: list[Video], channel_number: int, block: Block, moment: datetime) -> tuple[Video, ...]:
    ordered = sorted(videos, key=play_order_key)
    day = midnight_of(moment).date().isoformat()
    raw = f"{channel_number}|{block.start}|{day}|{','.join(block.tags)}"
    random.Random(zlib.crc32(raw.encode())).shuffle(ordered)
    return tuple(ordered)


def _days_since_anchor(moment: datetime) -> int:
    return (midnight_of(moment).date() - ANCHOR).days


def _block_at(channel: Channel, seconds: float) -> Block | None:
    for block in sorted(channel.blocks, key=lambda item: item.start):
        if block.start <= seconds < block.end:
            return block
    return None


def _gap(channel: Channel, moment: datetime, seconds: float) -> Airing:
    upcoming = [block.start for block in channel.blocks if block.start > seconds]
    if upcoming:
        end = _at_seconds(moment, min(upcoming))
    else:
        end = next_midnight(moment)
    previous = [block.end for block in channel.blocks if block.end <= seconds]
    if previous:
        start = _at_seconds(moment, max(previous))
    else:
        start = midnight_of(moment)
    if end <= moment:
        end = moment + timedelta(milliseconds=1)
    return Airing(channel.number, "Please stand by", None, start, end, 0.0, True)


def _slate(channel: Channel, start: datetime, end: datetime, title: str) -> Airing:
    if end <= start:
        end = start + timedelta(milliseconds=1)
    return Airing(channel.number, title, None, start, end, 0.0, True)


def _at_seconds(moment: datetime, seconds: int) -> datetime:
    return midnight_of(moment) + timedelta(seconds=seconds)


def _video_at(videos: tuple[Video, ...], position: float) -> tuple[Video, float]:
    elapsed = 0.0
    for index, video in enumerate(videos):
        end = elapsed + video.duration
        if position < end or index == len(videos) - 1:
            return video, max(0.0, position - elapsed)
        elapsed = end
    raise RuntimeError("block has no videos")
