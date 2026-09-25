"""
4H bar timing shared by the Telegram report and the backtester.

Bars are stamped with their open time in exchange time (US and TSX are both
Eastern). A bar closes 4 hours after it opens or at the 16:00 close,
whichever is first. Everything shown to the user is the bar close in
Mountain time on a 24-hour clock.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

_NY = ZoneInfo("America/New_York")
_MT = timezone(timedelta(hours=-6), "MDT")   # Alberta: permanent UTC-6, no DST


def bar_close_mt(bar_open: str | datetime) -> str:
    """'2026-09-24 13:30' (Eastern bar open) → '2026-09-24 14:00 MDT'."""
    if isinstance(bar_open, str):
        bar_open = datetime.strptime(bar_open, "%Y-%m-%d %H:%M")
    start = bar_open.replace(tzinfo=_NY)
    end = min(start + timedelta(hours=4), start.replace(hour=16, minute=0))
    return end.astimezone(_MT).strftime("%Y-%m-%d %H:%M %Z")
