"""Load and save channel lineups, and probe video durations with mpv."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from cablebox.schedule import (
    DAY_SECONDS,
    ORDER_SERIES,
    ORDER_SHUFFLE,
    Block,
    Channel,
    Video,
    normalize_tags,
)

GUIDE_HOURS_DEFAULT = 2.0
GUIDE_HOURS_MIN = 0.5
GUIDE_HOURS_MAX = 6.0


class LibraryError(Exception):
    """The lineup file could not be read."""


class ProbeError(Exception):
    """mpv could not report a duration for a video."""


class Library:
    def __init__(
        self,
        channels: list[Channel] | None = None,
        last_channel: int | None = None,
        guide_hours: float = GUIDE_HOURS_DEFAULT,
        videos: list[Video] | None = None,
        volume: int = 70,
    ) -> None:
        self.channels = list(channels or [])
        self.videos = list(videos or [])
        self.last_channel = last_channel
        self.guide_hours = _clamp_guide_hours(guide_hours)
        self.volume = _clamp_volume(volume)

    def to_dict(self) -> dict:
        return {
            "last_channel": self.last_channel,
            "guide_hours": self.guide_hours,
            "volume": self.volume,
            "videos": [_video_to_dict(video) for video in self.videos],
            "channels": [
                {
                    "number": channel.number,
                    "name": channel.name,
                    "blocks": [
                        {
                            "start": _format_clock_seconds(block.start),
                            "end": _format_clock_seconds(block.end),
                            "tags": list(block.tags),
                            "order": block.order,
                        }
                        for block in channel.blocks
                    ],
                }
                for channel in self.channels
            ],
        }


def load_library(path: Path) -> Library:
    if not path.exists():
        return Library()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LibraryError(f"Could not read {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise LibraryError(f"{path} is not a lineup file.")

    videos = [_video_from_dict(item) for item in raw.get("videos") or [] if isinstance(item, dict)]
    videos = [video for video in videos if video is not None]

    channels: list[Channel] = []
    for entry in raw.get("channels") or []:
        if not isinstance(entry, dict):
            continue
        try:
            number = int(entry["number"])
        except (KeyError, TypeError, ValueError):
            continue
        name = str(entry.get("name") or f"Channel {number}")
        blocks = [_block_from_dict(item) for item in entry.get("blocks") or [] if isinstance(item, dict)]
        blocks = [block for block in blocks if block is not None]
        if not blocks and entry.get("items"):
            imported, block = _import_legacy_items(videos, number, entry.get("items") or [])
            videos = imported
            if block is not None:
                blocks = [block]
        channels.append(Channel(number, name, tuple(blocks)))

    last = raw.get("last_channel")
    try:
        last_channel = int(last) if last is not None else None
    except (TypeError, ValueError):
        last_channel = None

    try:
        guide_hours = float(raw.get("guide_hours", GUIDE_HOURS_DEFAULT))
    except (TypeError, ValueError):
        guide_hours = GUIDE_HOURS_DEFAULT
    return Library(channels, last_channel, guide_hours, videos, _clamp_volume(raw.get("volume", 70)))


def save_library(path: Path, library: Library) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(library.to_dict(), indent=2)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(payload + "\n", encoding="utf-8")
    temporary.replace(path)


def parse_duration_output(text: str) -> float:
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            value = float(line)
        except ValueError:
            continue
        if value > 0:
            return value
    raise ProbeError("mpv did not report a duration.")


def probe_duration(path: Path, mpv_bin: str = "mpv") -> float:
    """Return the media duration in seconds, using the mpv executable."""
    if not path.is_file():
        raise ProbeError(f"File not found: {path}")
    command = [
        mpv_bin,
        "--no-config",
        "--quiet",
        "--vo=null",
        "--ao=null",
        "--frames=1",
        "--term-playing-msg=${=duration}",
        str(path),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ProbeError(
            "mpv was not found. Install mpv and put it on PATH before adding videos."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ProbeError(f"mpv timed out reading {path.name}.") from exc

    output = f"{completed.stdout}\n{completed.stderr}"
    try:
        return parse_duration_output(output)
    except ProbeError as exc:
        detail = output.strip() or f"mpv exited with status {completed.returncode}"
        raise ProbeError(f"Could not read the length of {path.name}. {detail}") from exc


def _video_to_dict(video: Video) -> dict:
    payload = {
        "path": video.path,
        "title": video.title,
        "duration": video.duration,
        "tags": list(video.tags),
    }
    if video.series is not None:
        payload["series"] = video.series
    if video.episode is not None:
        payload["episode"] = video.episode
    return payload


def _optional_series(value: object) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        if value < 0:
            return None
        return str(value)
    if isinstance(value, float):
        if value < 0 or value != int(value):
            return None
        return str(int(value))
    text = str(value).strip()
    return text or None


def _optional_count(value: object) -> int | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return number


def _clamp_volume(value: object) -> int:
    if isinstance(value, bool) or value is None:
        return 70
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 70
    return max(0, min(100, number))


def _clamp_guide_hours(value: float) -> float:
    if value < GUIDE_HOURS_MIN:
        return GUIDE_HOURS_MIN
    if value > GUIDE_HOURS_MAX:
        return GUIDE_HOURS_MAX
    return value


def _video_from_dict(item: dict) -> Video | None:
    if "path" not in item:
        return None
    path = str(item["path"])
    try:
        duration = float(item.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    title = str(item.get("title") or Path(path).stem)
    raw_tags = item.get("tags") or []
    if isinstance(raw_tags, str):
        tags = normalize_tags(raw_tags.split(","))
    else:
        tags = normalize_tags(str(tag) for tag in raw_tags)
    return Video(path, title, duration, tags, _optional_series(item.get("series")), _optional_count(item.get("episode")))


def _block_from_dict(item: dict) -> Block | None:
    start = _parse_clock_seconds(item.get("start"))
    end = _parse_clock_seconds(item.get("end"))
    if start is None or end is None or end <= start or start < 0 or end > DAY_SECONDS:
        return None
    raw_tags = item.get("tags") or []
    if isinstance(raw_tags, str):
        tags = normalize_tags(raw_tags.split(","))
    else:
        tags = normalize_tags(str(tag) for tag in raw_tags)
    order = str(item.get("order") or ORDER_SERIES)
    if order not in (ORDER_SERIES, ORDER_SHUFFLE):
        order = ORDER_SERIES
    return Block(start, end, tags, order)


def _import_legacy_items(videos: list[Video], number: int, items: list) -> tuple[list[Video], Block | None]:
    tag = f"imported-{number}"
    by_path = {video.path: video for video in videos}
    ordered: list[Video] = list(videos)
    found = False
    for item in items:
        if not isinstance(item, dict) or "path" not in item:
            continue
        path = str(item["path"])
        try:
            duration = float(item.get("duration") or 0)
        except (TypeError, ValueError):
            duration = 0.0
        title = str(item.get("title") or Path(path).stem)
        existing = by_path.get(path)
        if existing is None:
            tags = normalize_tags((tag,))
            video = Video(path, title, duration, tags)
            ordered.append(video)
        else:
            tags = normalize_tags((*existing.tags, tag))
            video = Video(path, existing.title, existing.duration, tags, existing.series, existing.episode)
            ordered = [video if current.path == path else current for current in ordered]
        by_path[path] = video
        found = True
    if not found:
        return videos, None
    return ordered, Block(0, DAY_SECONDS, (tag,), ORDER_SERIES)


def _format_clock_seconds(seconds: int) -> str:
    if seconds >= DAY_SECONDS:
        return "24:00"
    hours, remainder = divmod(int(seconds), 3600)
    minutes = remainder // 60
    return f"{hours:02d}:{minutes:02d}"


def _parse_clock_seconds(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value or "").strip()
    if text == "24:00":
        return DAY_SECONDS
    parts = text.split(":")
    if len(parts) < 2:
        return None
    try:
        hours = int(parts[0])
        minutes = int(parts[1])
    except ValueError:
        return None
    if hours == 24 and minutes == 0:
        return DAY_SECONDS
    return hours * 3600 + minutes * 60
