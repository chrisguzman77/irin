"""The one clock. Every timer and timestamp in the backend reads this.

`clock.now()` returns a naive local datetime. In live mode it tracks the
wall clock. The replay datasource calls `clock.set(speed, start)` so
that `now()` starts at the scenario's first timestamp and runs `speed`
times faster than wall time; `clock.advance(seconds)` jumps forward
(the replay seek, R12, and every test). `clock.sleep(seconds)` sleeps
`seconds` of CLOCK time (wall time / speed), so tasks that wait "5
minutes" wait 5 s of wall time at 60x.

This module is the ONLY place time.time(), time.monotonic(), or sleep()
may be called. Nothing else in app/, rounds/, buddy/, or the relay client
touches them.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta


class Clock:
    def __init__(self) -> None:
        self.reset()

    # --- control (replay, seek, tests, the mode switch) ---

    def reset(self) -> None:
        """Live mode: wall time, 1x. The mode switch calls this on return to live."""
        self._speed = 1.0
        self._start = datetime.now()
        self._mono0 = time.monotonic()
        self._offset = 0.0  # seconds of clock time added by advance()

    def set(self, speed: float = 1.0, start: datetime | None = None) -> None:
        """Replay mode: clock time starts at `start` (default: now) and runs `speed`x."""
        if speed <= 0:
            raise ValueError("speed must be positive")
        self._speed = float(speed)
        self._start = start or datetime.now()
        self._mono0 = time.monotonic()
        self._offset = 0.0

    def resync(self) -> None:
        """Live mode, after NTP sets the wall clock: re-anchor `now()` to the
        corrected wall time while keeping `elapsed()` continuous, so staleness
        (monotonic) is untouched and no relative timer jumps. Never used in
        replay, where the clock is the scenario's."""
        self._start = datetime.now() - timedelta(seconds=self.elapsed())

    def advance(self, seconds: float) -> None:
        """Jump clock time forward (tests, the replay seek). Never backwards."""
        if seconds < 0:
            raise ValueError("clock never runs backwards")
        self._offset += float(seconds)

    # --- reads (everything else) ---

    @property
    def speed(self) -> float:
        return self._speed

    def elapsed(self) -> float:
        """Seconds of clock time since reset/set: monotonic, immune to wall-clock
        changes. Staleness is measured with this, never with wall time."""
        return (time.monotonic() - self._mono0) * self._speed + self._offset

    def now(self) -> datetime:
        return self._start + timedelta(seconds=self.elapsed())

    async def sleep(self, clock_seconds: float) -> None:
        """Sleep `clock_seconds` of clock time (wall time divided by speed)."""
        await asyncio.sleep(max(0.0, clock_seconds / self._speed))


clock = Clock()
