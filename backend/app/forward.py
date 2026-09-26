"""C1: the outbound forwarder to Irin Cloud. Every FORWARD_INTERVAL_S of
clock time, POST {CLOUD_URL}/v1/ingest {device_id, readings[], alarm_events[],
low_events[], treatments[]} with everything new since the last ACCEPTED
batch: outbound only, batched, its own task, so it never blocks the poller
or an alarm; on any failure it keeps its cursor and retries next tick (the
Pi works with no cloud at all, invariant 21). Replay readings carry
is_demo=True and the cloud files them under a separate demo device_id.

Alarm events and low events join when their recorders land (R2, R4): the
sources are injectable callables that default to empty lists.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

import httpx

from . import store
from .clock import clock
from .config import config

log = logging.getLogger("irin.forward")

FORWARD_INTERVAL_S = 300.0  # clock seconds between batches
BACKFILL_HOURS = 24  # first run: how far back the cursor starts
CURSOR_KEY = "forward_cursor"  # store.kv: the timestamp of the newest reading the cloud accepted
Rows = Callable[[datetime], list[dict[str, Any]]]  # rows with timestamp > cursor, as JSON dicts


def _none(_since: datetime) -> list[dict[str, Any]]:
    return []


@dataclass
class Forwarder:
    readings_since: Rows
    treatments_since: Rows = _none
    alarm_events_since: Rows = _none
    low_events_since: Rows = _none
    is_demo: Callable[[], bool] = lambda: True
    cloud_url: str = ""
    device_id: str = ""
    device_token: str = ""
    transport: httpx.AsyncBaseTransport | None = None  # tests inject a MockTransport
    cursor: datetime | None = None
    sent_batches: int = 0
    failures: int = 0
    last_error: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.cloud_url and self.device_id and self.device_token)

    def load_cursor(self) -> datetime:
        if self.cursor is None:
            raw = store.get_kv(CURSOR_KEY)
            self.cursor = datetime.fromisoformat(raw) if raw else clock.now() - timedelta(hours=BACKFILL_HOURS)
        return self.cursor

    def batch(self) -> dict[str, Any] | None:
        """Everything newer than the cursor, or None when there is nothing to send."""
        since = self.load_cursor()
        demo = self.is_demo()
        lists = {
            "readings": self.readings_since(since),
            "treatments": self.treatments_since(since),
            "alarm_events": self.alarm_events_since(since),
            "low_events": self.low_events_since(since),
        }
        if not any(lists.values()):
            return None
        for rows in lists.values():
            for row in rows:
                row.setdefault("is_demo", demo)
        return {"device_id": self.device_id, **lists}

    async def tick(self) -> bool:
        """One attempt. True when the cloud accepted the batch (or there was
        nothing to send). Never raises."""
        if not self.enabled:
            return False
        try:
            payload = self.batch()
            if payload is None:
                return True
            async with httpx.AsyncClient(transport=self.transport, timeout=10.0) as client:
                r = await client.post(f"{self.cloud_url.rstrip('/')}/v1/ingest", json=payload,
                                      headers={"X-Device-Id": self.device_id, "X-Device-Token": self.device_token})
            if r.status_code != 200:
                raise RuntimeError(f"cloud answered {r.status_code}")
        except Exception as e:  # unreachable, refused, 503, bad JSON: keep the cursor, try again next tick
            self.failures += 1
            self.last_error = f"{type(e).__name__}: {e}"[:200]
            log.warning("forward failed (%s); cursor kept", type(e).__name__)
            return False
        newest = max(datetime.fromisoformat(row["timestamp"]) for row in payload["readings"]) if payload["readings"] else None
        newest_t = max((datetime.fromisoformat(row["timestamp"]) for row in payload["treatments"]), default=None)
        candidates = [c for c in (newest, newest_t) if c is not None]
        if candidates:
            self.cursor = max(candidates)
            store.set_kv(CURSOR_KEY, self.cursor.isoformat())
        self.sent_batches += 1
        self.last_error = None
        return True

    async def run(self) -> None:
        if not self.enabled:
            log.info("forwarder disabled (CLOUD_URL, DEVICE_ID, or DEVICE_TOKEN unset)")
            return
        while True:
            await self.tick()
            await clock.sleep(FORWARD_INTERVAL_S)

    def status(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "cursor": self.cursor.isoformat() if self.cursor else None,
                "sent_batches": self.sent_batches, "failures": self.failures, "last_error": self.last_error}


def from_config(readings_since: Rows, treatments_since: Rows, is_demo: Callable[[], bool]) -> Forwarder:
    return Forwarder(readings_since=readings_since, treatments_since=treatments_since, is_demo=is_demo,
                     cloud_url=config.CLOUD_URL, device_id=config.DEVICE_ID, device_token=config.DEVICE_TOKEN)
