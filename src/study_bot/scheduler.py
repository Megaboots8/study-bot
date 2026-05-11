"""Sleep until a target weekday/time in a given timezone.

This is the v1 scheduler: study-bot's CLI waits until the next
occurrence of a hardcoded weekday + time before running its checks and
opening any Reddit submit tabs.  Future versions will read a
gitignored schedule file with per-post / per-subreddit times so each
post can have its own daily slot.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from datetime import date, datetime, time as dtime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# datetime.weekday(): Monday = 0 ... Sunday = 6
MONDAY = 0
TUESDAY = 1
WEDNESDAY = 2
THURSDAY = 3
FRIDAY = 4
SATURDAY = 5
SUNDAY = 6


def _zoneinfo(tz: str):
    from zoneinfo import ZoneInfo
    return ZoneInfo(tz)


def next_occurrence(
    weekday: int,
    hour: int,
    minute: int,
    tz: str = "America/New_York",
    *,
    now: Optional[datetime] = None,
) -> datetime:
    """Return the next datetime matching the given weekday/hour/minute in `tz`.

    If today is the target weekday and the target time is still in the
    future, returns today's target.  Otherwise returns the same weekday
    in a subsequent week.  The returned datetime is timezone-aware.

    Constructing the target via `datetime.combine(date, time, tzinfo=z)`
    rather than adding `timedelta(days=N)` to a `datetime` ensures we
    always land on the correct wall-clock time even across DST changes.
    """
    z = _zoneinfo(tz)
    if now is None:
        now = datetime.now(z)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=z)

    today = now.date()
    days_ahead = (weekday - now.weekday()) % 7
    target_date: date = today + timedelta(days=days_ahead)
    target_dt = datetime.combine(target_date, dtime(hour, minute), tzinfo=z)
    if target_dt <= now:
        target_date = target_date + timedelta(days=7)
        target_dt = datetime.combine(target_date, dtime(hour, minute), tzinfo=z)
    return target_dt


def apply_jitter(target: datetime, jitter_seconds: int) -> datetime:
    """Return ``target`` with a uniform random offset applied.

    The offset is drawn from a uniform distribution over
    [-jitter_seconds, +jitter_seconds] (inclusive on both ends, integer
    seconds).  ``jitter_seconds <= 0`` disables jitter and returns the
    original target unchanged.

    Used to spread the actual post-time around each scheduled slot so a
    crawler can't predict the exact moment study-bot will post.
    """
    if jitter_seconds <= 0:
        return target
    delta = random.randint(-jitter_seconds, jitter_seconds)
    return target + timedelta(seconds=delta)


def wait_until(
    target: datetime,
    log: Optional[logging.Logger] = None,
    stop_event: Optional[threading.Event] = None,
) -> bool:
    """Sleep until `target` (timezone-aware), checking once per minute.

    Re-checks the wall-clock every 60 s so that if the computer
    sleeps/suspends during the wait, we wake up reasonably close to the
    target instead of overshooting by the suspend duration.

    If `stop_event` is given and becomes set before `target` is reached,
    the function returns early.

    Returns True if the target was reached normally, False if the wait was
    interrupted by `stop_event`.
    """
    if log is None:
        log = logger

    tz = target.tzinfo
    now = datetime.now(tz)
    total = (target - now).total_seconds()
    if total <= 0:
        return True

    log.info(
        "Scheduled to run at %s (waiting %dh %dm)",
        target.strftime("%Y-%m-%d %H:%M %Z"),
        int(total // 3600),
        int((total % 3600) // 60),
    )
    while True:
        if stop_event is not None and stop_event.is_set():
            log.info("wait_until: stop_event set; aborting wait")
            return False
        now = datetime.now(tz)
        remaining = (target - now).total_seconds()
        if remaining <= 0:
            return True
        chunk = min(remaining, 30)
        if stop_event is not None:
            # Use Event.wait() so the sleep is interrupted immediately when
            # the stop_event fires rather than waiting until the chunk ends.
            stop_event.wait(timeout=chunk)
        else:
            time.sleep(chunk)
