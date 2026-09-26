"""C1: POST /v1/ingest {device_id, readings[], alarm_events[], low_events[],
treatments[]}. The device token is checked in main.py; here the batch is
validated and upserted into the hypertables with psycopg, so a retry of the
same batch never duplicates a row. Rows with is_demo land under
"<device_id>-demo" so real dashboards never show replay data (invariant 1).

Timestamps are the Pi's naive local time exactly as its API serves them
(no offset); the columns are `timestamp` (without time zone) so George's
22:00-origin night buckets need no time-zone arithmetic. The column
contract is in cloud/README.md; cloud/sql/001_hypertables.sql (George)
creates the tables.

Nothing here is ever read by a card, a rule, or an alarm (invariant 21).
"""

from __future__ import annotations

import os
from datetime import date, datetime

import psycopg
from pydantic import BaseModel, ConfigDict, Field

TIGER_URI = os.environ.get("TIGER_URI", "postgresql://postgres:postgres@localhost:5432/irin")
MAX_ROWS = 5000  # per list per batch; the forwarder sends 5-minute batches, a 24 h backfill is 288


class _Row(BaseModel):
    model_config = ConfigDict(extra="ignore")  # the contracts may grow fields; ingest keeps its columns
    is_demo: bool = False


class Reading(_Row):
    timestamp: datetime
    glucose_mgdl: float
    trend: str = ""
    source: str = ""
    is_stale: bool = False


class AlarmEvent(_Row):
    event_id: str
    tier: str
    started_at: datetime
    acknowledged_at: datetime | None = None
    ack_source: str | None = None
    escalated: bool = False
    rearm_count: int = 0
    crossed_actual: bool = False
    presence_during: str = "unknown"


class LowEvent(_Row):
    low_event_id: str
    night_date: date
    started_at: datetime
    nadir_mgdl: float
    nadir_at: datetime
    minutes_below_70: int
    auc_below_70: float
    recovery_slope: float | None = None
    carbs_logged_within_30min: bool = False
    inferred_unfelt: bool = False
    alarm_event_id: str | None = None


class Treatment(_Row):
    timestamp: datetime
    kind: str
    insulin_units: float | None = None
    carbs_g: float | None = None
    dose_label: str | None = None
    text: str | None = None
    confirmed: bool = False


class IngestBatch(BaseModel):
    device_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")
    readings: list[Reading] = Field(default_factory=list, max_length=MAX_ROWS)
    alarm_events: list[AlarmEvent] = Field(default_factory=list, max_length=MAX_ROWS)
    low_events: list[LowEvent] = Field(default_factory=list, max_length=MAX_ROWS)
    treatments: list[Treatment] = Field(default_factory=list, max_length=MAX_ROWS)


def _dev(device_id: str, row: _Row) -> str:
    return f"{device_id}-demo" if row.is_demo else device_id


_READINGS = """INSERT INTO readings (device_id, time, mgdl, trend, source, is_demo)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (device_id, time) DO UPDATE SET mgdl = EXCLUDED.mgdl, trend = EXCLUDED.trend,
  source = EXCLUDED.source, is_demo = EXCLUDED.is_demo"""

_ALARMS = """INSERT INTO alarm_events (device_id, started_at, event_id, tier, acknowledged_at, ack_source,
  escalated, rearm_count, crossed_actual, presence_during, is_demo)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (device_id, started_at, event_id) DO UPDATE SET tier = EXCLUDED.tier,
  acknowledged_at = EXCLUDED.acknowledged_at, ack_source = EXCLUDED.ack_source, escalated = EXCLUDED.escalated,
  rearm_count = EXCLUDED.rearm_count, crossed_actual = EXCLUDED.crossed_actual,
  presence_during = EXCLUDED.presence_during, is_demo = EXCLUDED.is_demo"""

_LOWS = """INSERT INTO low_events (device_id, started_at, low_event_id, night_date, nadir_mgdl, nadir_at,
  minutes_below_70, auc_below_70, recovery_slope, carbs_logged_within_30min, inferred_unfelt, alarm_event_id, is_demo)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (device_id, started_at, low_event_id) DO UPDATE SET night_date = EXCLUDED.night_date,
  nadir_mgdl = EXCLUDED.nadir_mgdl, nadir_at = EXCLUDED.nadir_at, minutes_below_70 = EXCLUDED.minutes_below_70,
  auc_below_70 = EXCLUDED.auc_below_70, recovery_slope = EXCLUDED.recovery_slope,
  carbs_logged_within_30min = EXCLUDED.carbs_logged_within_30min, inferred_unfelt = EXCLUDED.inferred_unfelt,
  alarm_event_id = EXCLUDED.alarm_event_id, is_demo = EXCLUDED.is_demo"""

_TREATMENTS = """INSERT INTO treatments (device_id, time, kind, insulin_units, carbs_g, dose_label, text, confirmed, is_demo)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (device_id, time, kind) DO UPDATE SET insulin_units = EXCLUDED.insulin_units,
  carbs_g = EXCLUDED.carbs_g, dose_label = EXCLUDED.dose_label, text = EXCLUDED.text,
  confirmed = EXCLUDED.confirmed, is_demo = EXCLUDED.is_demo"""


def ingest(batch: IngestBatch, uri: str | None = None) -> dict:
    """Upsert every list in one transaction. Returns the row counts received
    (an upsert of an already-stored row still counts: the Pi only needs to know
    the batch was accepted so it can advance its cursor)."""
    d = batch.device_id
    with psycopg.connect(uri or TIGER_URI) as conn, conn.transaction():
        with conn.cursor() as cur:
            if batch.readings:
                cur.executemany(_READINGS, [(_dev(d, r), r.timestamp, r.glucose_mgdl, r.trend, r.source, r.is_demo)
                                            for r in batch.readings])
            if batch.alarm_events:
                cur.executemany(_ALARMS, [(_dev(d, a), a.started_at, a.event_id, a.tier, a.acknowledged_at, a.ack_source,
                                           a.escalated, a.rearm_count, a.crossed_actual, a.presence_during, a.is_demo)
                                          for a in batch.alarm_events])
            if batch.low_events:
                cur.executemany(_LOWS, [(_dev(d, e), e.started_at, e.low_event_id, e.night_date, e.nadir_mgdl, e.nadir_at,
                                         e.minutes_below_70, e.auc_below_70, e.recovery_slope, e.carbs_logged_within_30min,
                                         e.inferred_unfelt, e.alarm_event_id, e.is_demo) for e in batch.low_events])
            if batch.treatments:
                cur.executemany(_TREATMENTS, [(_dev(d, t), t.timestamp, t.kind, t.insulin_units, t.carbs_g, t.dose_label,
                                               t.text, t.confirmed, t.is_demo) for t in batch.treatments])
    return {"stored": {"readings": len(batch.readings), "alarm_events": len(batch.alarm_events),
                       "low_events": len(batch.low_events), "treatments": len(batch.treatments)}}
