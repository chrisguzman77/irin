"""Nightscout datasource (chris.md step 3): a 60 s poller, a SQLite cache,
and MONOTONIC stale marking.

Staleness is clock.py's elapsed time since the last NEW reading arrived,
never a wall-clock difference against the reading's timestamp: the Pi has
no RTC battery, so its wall clock is wrong after any boot without network
and would mark fresh data stale (or stale data fresh). history() anchors on
the latest reading's timestamp for the same reason.

On start() the last cached reading is served marked stale until a fresh
poll lands (also the step 12 rule for returning to live). A network error
keeps the last reading and keeps polling; it never crashes the app.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable

import httpx

from .. import store
from ..clock import clock
from ..contracts import Reading
from .base import STALE_AFTER_MIN, DataSource

log = logging.getLogger("irin.nightscout")
logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx logs URLs at INFO; the token is a query param

POLL_SECONDS = 60
FETCH_COUNT = 24  # two hours of 5-minute entries per poll, so a short outage backfills
FetchFn = Callable[[], Awaitable[list[dict[str, Any]]]]


def entry_to_reading(entry: dict[str, Any]) -> Reading | None:
    """One Nightscout sgv entry -> Reading (naive local time). None if malformed."""
    try:
        ts = datetime.fromtimestamp(int(entry["date"]) / 1000.0)
        return Reading(timestamp=ts, glucose_mgdl=float(entry["sgv"]), trend=str(entry.get("direction", "NONE")),
                       source="nightscout", is_stale=False)
    except (KeyError, TypeError, ValueError):
        return None


class NightscoutDataSource(DataSource):
    name = "nightscout"

    def __init__(self, url: str, token: str, fetch: FetchFn | None = None) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self._fetch: FetchFn = fetch or self._http_fetch
        self._latest: Reading | None = None
        self._last_new_at: float | None = None  # clock.elapsed() when the last NEW timestamp arrived
        self._task: asyncio.Task | None = None
        self._failed = False

    # --- polling ---

    async def _http_fetch(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{self.url}/api/v1/entries/sgv.json",
                                 params={"count": FETCH_COUNT, "token": self.token})
            r.raise_for_status()
            return r.json()

    async def poll_once(self) -> bool:
        """One fetch. Returns True when a reading with a new timestamp arrived."""
        try:
            entries = await self._fetch()
        except Exception as e:  # network down, 5xx, bad JSON: keep the last reading, keep polling
            if not self._failed:
                log.warning("nightscout poll failed (%s); keeping the last reading", type(e).__name__)
            self._failed = True
            return False
        self._failed = False
        readings = [r for r in (entry_to_reading(e) for e in entries) if r is not None]
        if not readings:
            return False
        readings.sort(key=lambda r: r.timestamp)
        newest = readings[-1]
        if self._latest is not None and newest.timestamp <= self._latest.timestamp:
            return False  # nothing new: the staleness timer keeps running
        conn = store.connect()
        try:
            for r in readings:
                store.insert_reading(r, conn)
        finally:
            conn.close()
        self._latest = newest
        self._last_new_at = clock.elapsed()
        return True

    async def _loop(self) -> None:
        while True:
            await self.poll_once()
            await clock.sleep(POLL_SECONDS)

    async def start(self) -> None:
        clock.reset()  # live mode: wall time, 1x
        cached = store.select_latest_reading()
        if cached is not None:
            self._latest = cached  # served stale until a fresh poll lands
            self._last_new_at = None
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    # --- reads ---

    def is_stale(self) -> bool:
        if self._last_new_at is None:
            return True
        return clock.elapsed() - self._last_new_at >= STALE_AFTER_MIN * 60

    async def get_latest(self) -> Reading | None:
        if self._latest is None:
            return None
        return self._latest.model_copy(update={"is_stale": self.is_stale()})

    async def history(self, minutes: int) -> list[Reading]:
        if self._latest is None:
            return []
        since = self._latest.timestamp - timedelta(minutes=minutes)
        return [r.model_copy(update={"is_stale": False}) for r in store.select_readings(since)
                if r.timestamp <= self._latest.timestamp]

    async def seek(self, to: datetime) -> None:
        raise NotImplementedError("seek is replay-only")
