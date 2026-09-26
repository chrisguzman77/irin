"""Clock-time windows ("22:00" to "07:00"), shared by the alarm engine (quiet
hours for highs), the presence machine (the night rule), and the scheduler."""

from __future__ import annotations

from datetime import time


def parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def in_window(t: time, start: str, end: str) -> bool:
    """True when t is inside [start, end); a window may cross midnight."""
    a, b = parse_hhmm(start), parse_hhmm(end)
    if a <= b:
        return a <= t < b
    return t >= a or t < b
