#!/usr/bin/env python3
"""
Decides whether a scheduled full-scan run should go ahead.

4H bars close at 13:30 and 16:00 New York time. GitHub cron is UTC-only,
so full-scan.yml fires at both the EDT and EST UTC times for each bar close;
this guard lets through only the run whose *scheduled* time lands in the
hour after a bar close. Using the scheduled cron (not the actual start time)
means a delayed GitHub runner still scans instead of being skipped.

Manual runs (workflow_dispatch / Telegram /fullscan) always go ahead.

Writes run=true|false to $GITHUB_OUTPUT.
"""

import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

_NY = ZoneInfo("America/New_York")
_WINDOWS = [(13 * 60 + 30, 14 * 60 + 30), (16 * 60, 17 * 60)]   # minutes after midnight, NY


def should_run(cron: str, now_utc: datetime) -> bool:
    if not cron:
        return True
    minute, hour = (int(x) for x in cron.split()[:2])
    fired = now_utc.replace(hour=hour, minute=minute, second=0, microsecond=0)
    ny = fired.astimezone(_NY)
    t = ny.hour * 60 + ny.minute
    return any(start <= t < end for start, end in _WINDOWS)


def main():
    cron = os.environ.get("SCHEDULE_CRON", "").strip()
    run = should_run(cron, datetime.now(timezone.utc))
    print(f"cron='{cron or 'manual'}' → run={str(run).lower()}")

    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"run={str(run).lower()}\n")


if __name__ == "__main__":
    main()
