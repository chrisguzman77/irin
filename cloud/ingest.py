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
from psycopg.types.json import Jsonb
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


class NightRecord(_Row):
    night_date: date
    window_start: datetime
    window_end: datetime
    coverage_pct: float
    reason_codes: list[str] = Field(default_factory=list)
    code_source: str
    rise_mgdl: float | None = None
    low_point_mgdl: float | None = None
    tbr_pct: float | None = None
    minutes_below_70: int = 0
    near_miss_count: int = 0
    level2_count: int = 0


class Plan(_Row):
    plan_id: str
    drug_class: str
    drug_label: str
    steps: list[dict] = Field(default_factory=list)
    started_at: date
    status: str


class SymptomCheck(_Row):
    date: date
    gi: str


class BuddyEvent(_Row):
    event_id: str
    kind: str
    at: datetime
    confidence: str | None = None


class IngestBatch(BaseModel):
    device_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")
    readings: list[Reading] = Field(default_factory=list, max_length=MAX_ROWS)
    alarm_events: list[AlarmEvent] = Field(default_factory=list, max_length=MAX_ROWS)
    low_events: list[LowEvent] = Field(default_factory=list, max_length=MAX_ROWS)
    treatments: list[Treatment] = Field(default_factory=list, max_length=MAX_ROWS)
    night_records: list[NightRecord] = Field(default_factory=list, max_length=MAX_ROWS)
    plans: list[Plan] = Field(default_factory=list, max_length=MAX_ROWS)
    symptom_checks: list[SymptomCheck] = Field(default_factory=list, max_length=MAX_ROWS)
    buddy_events: list[BuddyEvent] = Field(default_factory=list, max_length=MAX_ROWS)


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


_NIGHTS = """INSERT INTO night_records (device_id, night_date, window_start, window_end, coverage_pct, reason_codes,
  code_source, rise_mgdl, low_point_mgdl, tbr_pct, minutes_below_70, near_miss_count, level2_count, is_demo)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (device_id, night_date) DO UPDATE SET window_start = EXCLUDED.window_start,
  window_end = EXCLUDED.window_end, coverage_pct = EXCLUDED.coverage_pct, reason_codes = EXCLUDED.reason_codes,
  code_source = EXCLUDED.code_source, rise_mgdl = EXCLUDED.rise_mgdl, low_point_mgdl = EXCLUDED.low_point_mgdl,
  tbr_pct = EXCLUDED.tbr_pct, minutes_below_70 = EXCLUDED.minutes_below_70,
  near_miss_count = EXCLUDED.near_miss_count, level2_count = EXCLUDED.level2_count, is_demo = EXCLUDED.is_demo"""

_PLANS = """INSERT INTO plans (device_id, started_at, plan_id, drug_class, drug_label, steps, status, is_demo)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (device_id, started_at, plan_id) DO UPDATE SET drug_class = EXCLUDED.drug_class,
  drug_label = EXCLUDED.drug_label, steps = EXCLUDED.steps, status = EXCLUDED.status, is_demo = EXCLUDED.is_demo"""

_CHECKS = """INSERT INTO symptom_checks (device_id, date, gi, is_demo) VALUES (%s, %s, %s, %s)
ON CONFLICT (device_id, date) DO UPDATE SET gi = EXCLUDED.gi, is_demo = EXCLUDED.is_demo"""

_BUDDY = """INSERT INTO buddy_events (device_id, at, event_id, kind, confidence, is_demo) VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (device_id, at, event_id) DO UPDATE SET kind = EXCLUDED.kind, confidence = EXCLUDED.confidence,
  is_demo = EXCLUDED.is_demo"""


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
            if batch.night_records:
                cur.executemany(_NIGHTS, [(_dev(d, n), n.night_date, n.window_start, n.window_end, n.coverage_pct,
                                           list(n.reason_codes), n.code_source, n.rise_mgdl, n.low_point_mgdl, n.tbr_pct,
                                           n.minutes_below_70, n.near_miss_count, n.level2_count, n.is_demo)
                                          for n in batch.night_records])
            if batch.plans:
                cur.executemany(_PLANS, [(_dev(d, p), p.started_at, p.plan_id, p.drug_class, p.drug_label, Jsonb(p.steps),
                                          p.status, p.is_demo) for p in batch.plans])
            if batch.symptom_checks:
                cur.executemany(_CHECKS, [(_dev(d, c), c.date, c.gi, c.is_demo) for c in batch.symptom_checks])
            if batch.buddy_events:
                cur.executemany(_BUDDY, [(_dev(d, b), b.at, b.event_id, b.kind, b.confidence, b.is_demo)
                                         for b in batch.buddy_events])
    return {"stored": {name: len(getattr(batch, name)) for name in
                       ("readings", "alarm_events", "low_events", "treatments", "night_records", "plans",
                        "symptom_checks", "buddy_events")}}
