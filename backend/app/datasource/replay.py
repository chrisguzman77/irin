"""Replay datasource: plays a scenario CSV (timestamp, glucose_mgdl, trend)
through clock.py at REPLAY_SPEED. On start it sets the clock to the
scenario's first timestamp at `speed`x, so night windows, staleness, and
every timer line up with the scenario. Every reading is source="replay",
and every screen badges DEMO while this source is active (invariant 1)."""

from __future__ import annotations

import csv
from bisect import bisect_right
from datetime import datetime, timedelta
from pathlib import Path

from ..clock import clock
from ..contracts import Reading
from .base import STALE_AFTER_MIN, DataSource


class ReplayDataSource(DataSource):
    name = "replay"

    def __init__(self, scenario_path: str | Path, speed: float = 60.0) -> None:
        self.path = Path(scenario_path)
        self.speed = speed
        self.rows: list[tuple[datetime, float, str]] = self._load(self.path)
        if not self.rows:
            raise ValueError(f"scenario has no rows: {self.path}")
        self._times: list[datetime] = [r[0] for r in self.rows]
        self._paused_at: datetime | None = None  # the feed stops here while the clock runs on
        self._overlay: list[tuple[datetime, float, str]] = []  # injected readings; the CSV stays clean

    @staticmethod
    def _load(path: Path) -> list[tuple[datetime, float, str]]:
        rows: list[tuple[datetime, float, str]] = []
        with path.open(newline="") as f:
            for r in csv.DictReader(f):
                rows.append((datetime.fromisoformat(r["timestamp"]), float(r["glucose_mgdl"]), r["trend"]))
        rows.sort(key=lambda r: r[0])
        return rows

    async def start(self) -> None:
        clock.set(speed=self.speed, start=self.rows[0][0])

    async def stop(self) -> None:
        clock.reset()

    # --- the demo panel (step 12) ---

    def set_paused(self, paused: bool) -> None:
        """Pause the FEED, not the clock: no reading newer than the pause moment
        arrives, so after STALE_AFTER_MIN the latest reading is honestly stale."""
        if paused and self._paused_at is None:
            self._paused_at = clock.now()
        elif not paused:
            self._paused_at = None

    def inject(self, glucose_mgdl: float, trend: str) -> Reading:
        """Overlay one reading at the current clock time. rows is never touched."""
        row = (self._cutoff(clock.now()), float(glucose_mgdl), trend)  # visible even while paused
        self._overlay.append(row)
        return self._to_reading(row)

    def _cutoff(self, now: datetime) -> datetime:
        return min(now, self._paused_at) if self._paused_at is not None else now

    def _available(self, now: datetime) -> list[tuple[datetime, float, str]]:
        """CSV rows plus injected readings with timestamp <= the feed cutoff
        (rows are sorted; a bisect, not a scan; an overlay row with the same
        timestamp as a CSV row sorts after it)."""
        cutoff = self._cutoff(now)
        rows = self.rows[: bisect_right(self._times, cutoff)]
        if not self._overlay:
            return rows
        return sorted(rows + [o for o in self._overlay if o[0] <= cutoff], key=lambda r: r[0])

    @staticmethod
    def _to_reading(row: tuple[datetime, float, str], stale: bool = False) -> Reading:
        ts, mgdl, trend = row
        return Reading(timestamp=ts, glucose_mgdl=mgdl, trend=trend, source="replay", is_stale=stale)

    async def get_latest(self) -> Reading | None:
        """Staleness is a property of the FEED: the latest reading is stale when no
        newer one has arrived for STALE_AFTER_MIN clock minutes."""
        now = clock.now()
        avail = self._available(now)
        if not avail:
            return None
        last = avail[-1]
        return self._to_reading(last, stale=(now - last[0]) >= timedelta(minutes=STALE_AFTER_MIN))

    async def history(self, minutes: int) -> list[Reading]:
        now = clock.now()
        since = now - timedelta(minutes=minutes)
        # History rows are facts, never "stale"; gaps are read from their timestamps.
        return [self._to_reading(r) for r in self._available(now) if r[0] >= since]

    async def seek(self, to: datetime) -> None:
        """R12: bulk-load readings up to `to`, advance clock.py to `to`, and let
        the engine's idempotent catch-up generate every card that should exist
        by then. Seeking twice never duplicates a card."""
        raise NotImplementedError("R12: replay seek")
