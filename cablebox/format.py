"""Clock and duration strings for the guide and the lineup editor."""

from __future__ import annotations

from datetime import datetime

from cablebox.schedule import DAY_SECONDS


def format_clock(moment: datetime) -> str:
    hour = moment.hour % 12 or 12
    suffix = "AM" if moment.hour < 12 else "PM"
    if moment.second:
        return f"{hour}:{moment.minute:02d}:{moment.second:02d} {suffix}"
    return f"{hour}:{moment.minute:02d} {suffix}"


def format_range(start: datetime, end: datetime) -> str:
    return f"{format_clock(start)} – {format_clock(end)}"


def format_day_offset(seconds: float) -> str:
    whole = int(round(seconds))
    if whole >= DAY_SECONDS:
        whole = 0
    elif whole < 0:
        whole = 0
    hours, remainder = divmod(whole, 3600)
    minutes, secs = divmod(remainder, 60)
    return format_clock(datetime(2000, 1, 1, hours, minutes, secs))


def format_duration(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    if minutes and secs:
        return f"{minutes}m {secs}s"
    if minutes:
        return f"{minutes}m"
    return f"{secs}s"
