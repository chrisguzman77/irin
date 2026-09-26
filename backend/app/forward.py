"""C1: the outbound forwarder to Irin Cloud. Every FORWARD_INTERVAL_S of
clock time, POST {CLOUD_URL}/v1/ingest {device_id, readings[], alarm_events[],
low_events[], treatments[]} with everything new since the last ACCEPTED
batch: outbound only, batched, its own task, so it never blocks the poller
or an alarm; a down cloud keeps every cursor and retries next tick (the Pi
works with no cloud at all, invariant 21).

Cursors: the store-backed lists (the live readings cache, treatments) advance
by SQLite rowid, i.e. insertion order, so a reading that arrives late with an
older timestamp is still forwarded and a re-cached row is upserted again; the
cursors are persisted in store.kv. Replay readings are not in the store: they
advance by timestamp within one scenario run and reset whenever the replay
source object changes (mode switch, scenario select), and are flagged is_demo.
A treatment carries the is_demo it was logged under, so an entry typed during
a demo never lands on the real device. The cloud files is_demo rows under a
separate device id.

Alarm events and low events join when their recorders land (R2, R4).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

import httpx

from . import store
from .clock import clock
from .config import config

log = logging.getLogger("irin.forward")

FORWARD_INTERVAL_S = 300.0  # clock seconds between batches
BATCH_ROWS = 2000  # per store-backed list per batch (the cloud caps at 5000); a big cache drains over ticks
RowidRows = Callable[[int, int], list[tuple[int, dict[str, Any]]]]  # (after_rowid, limit) -> [(rowid, row)]
ReplayRows = Callable[[datetime | None], tuple[Any, list[dict[str, Any]]]]  # cursor -> (run key, rows newer)


def _no_rows(_after: int, _limit: int) -> list[tuple[int, dict[str, Any]]]:
    return []


def _no_replay(_cursor: datetime | None) -> tuple[Any, list[dict[str, Any]]]:
    return None, []


@dataclass
class Forwarder:
    readings: RowidRows = _no_rows  # the live cache
    treatments: RowidRows = _no_rows
    alarm_events: RowidRows = _no_rows  # R2
    low_events: RowidRows = _no_rows  # R4
    replay_readings: ReplayRows = _no_replay  # (run key, rows) while the replay source is active
    cloud_url: str = ""
    device_id: str = ""
    device_token: str = ""
    transport: httpx.AsyncBaseTransport | None = None  # tests inject a MockTransport
    cursors: dict[str, int] = field(default_factory=dict)  # list name -> last accepted rowid
    replay_run: Any = None
    replay_cursor: datetime | None = None
    sent_batches: int = 0
    failures: int = 0
    dropped_batches: int = 0  # rejected by the cloud (4xx): logged and skipped, never retried forever
    last_error: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.cloud_url and self.device_id and self.device_token)

    def _cursor(self, name: str) -> int:
        if name not in self.cursors:
            raw = store.get_kv(f"forward_rowid:{name}")
            self.cursors[name] = int(raw) if raw else 0
        return self.cursors[name]

    def batch(self) -> tuple[dict[str, Any], dict[str, int], datetime | None] | None:
        """(payload, new rowid cursors, new replay cursor), or None when nothing is new."""
        sources = {"readings": self.readings, "treatments": self.treatments,
                   "alarm_events": self.alarm_events, "low_events": self.low_events}
        lists: dict[str, list[dict[str, Any]]] = {}
        advance: dict[str, int] = {}
        for name, fn in sources.items():
            rows = fn(self._cursor(name), BATCH_ROWS)
            lists[name] = [row for _, row in rows]
            if rows:
                advance[name] = rows[-1][0]
        run, replay_rows = self.replay_readings(self.replay_cursor if self.replay_run is not None else None)
        if run is not self.replay_run:  # a new scenario run: start from its first row
            self.replay_run, self.replay_cursor = run, None
            run, replay_rows = self.replay_readings(None)
        replay_cursor = self.replay_cursor
        if replay_rows:
            for row in replay_rows:
                row["is_demo"] = True
            lists["readings"] = lists["readings"] + replay_rows
            replay_cursor = max(datetime.fromisoformat(r["timestamp"]) for r in replay_rows)
        if not any(lists.values()):
            return None
        return {"device_id": self.device_id, **lists}, advance, replay_cursor

    def _commit(self, advance: dict[str, int], replay_cursor: datetime | None) -> None:
        for name, rowid in advance.items():
            self.cursors[name] = rowid
            store.set_kv(f"forward_rowid:{name}", str(rowid))
        self.replay_cursor = replay_cursor

    async def tick(self) -> bool:
        """One attempt. True when the cloud accepted the batch (or nothing was
        new). Never raises; a failure keeps every cursor."""
        if not self.enabled:
            return False
        try:
            prepared = self.batch()
            if prepared is None:
                return True
            payload, advance, replay_cursor = prepared
            async with httpx.AsyncClient(transport=self.transport, timeout=10.0) as client:
                r = await client.post(f"{self.cloud_url.rstrip('/')}/v1/ingest", json=payload,
                                      headers={"X-Device-Id": self.device_id, "X-Device-Token": self.device_token})
            if 400 <= r.status_code < 500 and r.status_code not in (401, 403, 408, 429):
                # the cloud will never accept these rows: skip them rather than retry the same window forever
                self.dropped_batches += 1
                self.last_error = f"cloud rejected a batch: {r.status_code} {r.text[:120]}"
                log.error("forward: %s; batch skipped", self.last_error)
                self._commit(advance, replay_cursor)
                return False
            if r.status_code != 200:
                raise RuntimeError(f"cloud answered {r.status_code}")
            self._commit(advance, replay_cursor)
        except Exception as e:  # unreachable, refused, 503, a locked sqlite: keep the cursors, try again next tick
            self.failures += 1
            self.last_error = f"{type(e).__name__}: {e}"[:200]
            log.warning("forward failed (%s); cursors kept", type(e).__name__)
            return False
        self.sent_batches += 1
        self.last_error = None
        return True

    async def run(self) -> None:
        if not self.enabled:
            log.info("forwarder disabled (CLOUD_URL, DEVICE_ID, or DEVICE_TOKEN unset)")
            return
        while True:
            try:
                await self.tick()
            except Exception:  # belt and braces: the task never dies
                log.exception("forwarder tick raised")
            await clock.sleep(FORWARD_INTERVAL_S)

    def status(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "cursors": dict(self.cursors),
                "replay_cursor": self.replay_cursor.isoformat() if self.replay_cursor else None,
                "sent_batches": self.sent_batches, "failures": self.failures,
                "dropped_batches": self.dropped_batches, "last_error": self.last_error}


def from_config(replay_readings: ReplayRows) -> Forwarder:
    return Forwarder(readings=store.select_reading_rows, treatments=store.select_treatment_rows,
                     replay_readings=replay_readings, cloud_url=config.CLOUD_URL, device_id=config.DEVICE_ID,
                     device_token=config.DEVICE_TOKEN)
