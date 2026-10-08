from __future__ import annotations

import math
import time


QUARTER_HOUR_SECONDS = 900


def next_quarter_hour(timestamp: float) -> float:
    """Next UTC quarter-hour boundary; Prague has the same minute slots."""
    if not math.isfinite(timestamp):
        raise ValueError("clock timestamp must be finite")
    return (math.floor(timestamp / QUARTER_HOUR_SECONDS) + 1) * QUARTER_HOUR_SECONDS


def wait_for_quarter_hour(*, clock=time.time, sleep=time.sleep) -> None:
    # Short waits recheck wall time after clock adjustments and allow Ctrl+C.
    target = next_quarter_hour(clock())
    while True:
        now = clock()
        if target - now > QUARTER_HOUR_SECONDS:
            target = next_quarter_hour(now)
        if now >= target:
            # A forward clock jump must not launch a catch-up cycle off schedule.
            if now - target < 1:
                return
            target = next_quarter_hour(now)
        sleep(min(30.0, target - now))
