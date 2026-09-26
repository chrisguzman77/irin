"""Replay datasource: plays a scenario CSV (timestamp, glucose_mgdl, trend)
through clock.py at REPLAY_SPEED. On start it sets the clock to the
scenario's first timestamp at `speed`x, so night windows, staleness, and
every timer line up with the scenario. Every reading is source="replay",
and every screen badges DEMO while this source is active (invariant 1)."""

from __future__ import annotations

import csv
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

    def _available(self, now: datetime) -> list[tuple[datetime, float, str]]:
        return [r for r in self.rows if r[0] <= now]

    def _to_reading(self, row: tuple[datetime, float, str], now: datetime) -> Reading:
        ts, mgdl, trend = row
        stale = (now - ts) >= timedelta(minutes=STALE_AFTER_MIN)
        return Reading(timestamp=ts, glucose_mgdl=mgdl, trend=trend, source="replay", is_stale=stale)

    async def get_latest(self) -> Reading | None:
        now = clock.now()
        avail = self._available(now)
        if not avail:
            return None
        return self._to_reading(avail[-1], now)

    async def history(self, minutes: int) -> list[Reading]:
        now = clock.now()
        since = now - timedelta(minutes=minutes)
        return [self._to_reading(r, now) for r in self._available(now) if r[0] >= since]

    async def seek(self, to: datetime) -> None:
        """R12: bulk-load readings up to `to`, advance clock.py to `to`, and let
        the engine's idempotent catch-up generate every card that should exist
        by then. Seeking twice never duplicates a card."""
        raise NotImplementedError("R12: replay seek")
