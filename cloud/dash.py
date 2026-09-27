"""C3: GET /v1/dash/{name}?days=: nights, tir, profile, lows_heatmap, alarms,
near_misses, basal, sensor, step_watch, buddy, under_the_hood; each a thin
query over one aggregate or view (cloud/sql/002, 004) returning JSON the
chart draws directly. Dashboards draw pictures and never decide (invariant
21): nothing here is read by a card, a rule, or an alarm. `nights` reads the
`nightly` view, which ml/tests/test_agreement.py proves equal to
ml/models/nights.py. `tir` is NOT the same number as anything in nights.py
(it is the share of readings PRESENT, the usual time-in-range), so it is
never compared with a card.

Every response: {name, device_id, is_demo, days, as_of, available, ...}.
The window ends at the device's NEWEST reading (as_of), not at the server's
clock, so replayed demo data (2021 timestamps) and history draw the same way.
No data -> "empty": true, never zeros drawn as data. step_watch and buddy
read the four lists cloud/sql/005 adds (night records, plans, symptom
checks, buddy events); step_watch's baseline low point is the number
nights.step_window_metrics calls baseline_low_point, asserted equal in
cloud/tests/test_dash.py.

Also family_last_night(device_id, is_demo): the rollup behind
GET /v1/family/last_night (fields pinned in cloud/README.md)."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta

import psycopg
from psycopg.rows import dict_row

log = logging.getLogger("irin.cloud.dash")

TIGER_URI = os.environ.get("TIGER_URI", "postgresql://postgres:postgres@localhost:5432/irin")
DEVICE_TZ = os.environ.get("DEVICE_TZ", "America/New_York")   # the Pi's local zone: its timestamps are naive local
STALE_MIN = 15
NIGHT_START_H, NIGHT_HOURS = 22, 9
MAX_DAYS = 3650
BASELINE_NIGHTS, COVERAGE_OK_PCT = 14, 85.0   # nights.py / step_watch.py: the 14 nights before the start, >= 85%


def _connect():
    return psycopg.connect(TIGER_URI, connect_timeout=10, row_factory=dict_row)


def device_now() -> datetime:
    """The device's local wall time, naive, comparable with stored timestamps."""
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(DEVICE_TZ)).replace(tzinfo=None)
    except Exception:  # no tz database in the image: server local time, logged
        log.warning("DEVICE_TZ %s unavailable; using the server's local time", DEVICE_TZ)
        return datetime.now()


# ---------------------------------------------------------------- one handler per dashboard

def _nights(conn, dev, start, end):
    rows = conn.execute(
        "SELECT night_date, readings, coverage_pct::float8 AS coverage_pct, low_point_mgdl::float8 AS low_point_mgdl,"
        " minutes_below_70, first_reading, last_reading FROM nightly"
        " WHERE device_id = %s AND night_date >= %s AND night_date <= %s ORDER BY night_date",
        (dev, start.date(), end.date())).fetchall()
    return {"rows": rows, "reason_codes": None,
            "note": "reason codes are not forwarded to the cloud; tiles can color by coverage and low point"}


def _tir(conn, dev, start, end):
    rows = conn.execute(
        "SELECT day::date AS day, readings, avg_mgdl::float8 AS avg_mgdl, share_under_70::float8 AS share_under_70,"
        " share_70_180::float8 AS share_70_180, share_over_180::float8 AS share_over_180 FROM daily_stats"
        " WHERE device_id = %s AND day >= %s AND day <= %s ORDER BY day",
        (dev, start - timedelta(days=30), end)).fetchall()
    for k, r in enumerate(rows):                                   # 7- and 30-day lines over days present
        for span in (7, 30):
            win = [x["share_70_180"] for x in rows[max(0, k - span + 1):k + 1]
                   if x["day"] > r["day"] - timedelta(days=span)]
            r[f"in_range_{span}d"] = sum(win) / len(win) if win else None
    return {"rows": [r for r in rows if r["day"] >= start.date()]}


def _profile(conn, dev, start, end):
    rows = conn.execute(
        "SELECT (bucket::time) AS time_of_day,"
        " approx_percentile(0.1, rollup(pct)) AS p10, approx_percentile(0.5, rollup(pct)) AS p50,"
        " approx_percentile(0.9, rollup(pct)) AS p90, sum(readings)::int AS readings"
        " FROM overnight_profile WHERE device_id = %s AND bucket >= %s AND bucket <= %s"
        " GROUP BY 1 ORDER BY 1", (dev, start, end)).fetchall()
    return {"rows": rows}


def _lows_heatmap(conn, dev, start, end):
    rows = conn.execute(
        "SELECT EXTRACT(ISODOW FROM hour)::int AS weekday, EXTRACT(HOUR FROM hour)::int AS hour_of_day,"
        " sum(under_70)::int AS under_70, sum(readings)::int AS readings,"
        " (sum(under_70)::float8 / NULLIF(sum(readings), 0)) AS share_under_70"
        " FROM hourly_heatmap WHERE device_id = %s AND hour >= %s AND hour <= %s GROUP BY 1, 2 ORDER BY 1, 2",
        (dev, start, end)).fetchall()
    return {"rows": rows, "weekday": "ISO: 1 = Monday .. 7 = Sunday"}


def _alarms(conn, dev, start, end):
    rows = conn.execute(
        "SELECT week, tier, alarms::int AS alarms, escalated::int AS escalated, rearms::int AS rearms,"
        " mean_ack_min::float8 AS mean_ack_min FROM alarms_weekly"
        " WHERE device_id = %s AND week >= %s AND week <= %s ORDER BY week, tier",
        (dev, start - timedelta(days=7), end)).fetchall()
    return {"rows": rows}


def _near_misses(conn, dev, start, end):
    rows = conn.execute(
        "SELECT week, near_misses::int AS near_misses FROM near_misses_weekly"
        " WHERE device_id = %s AND week >= %s AND week <= %s ORDER BY week",
        (dev, start - timedelta(days=7), end)).fetchall()
    return {"rows": rows}


def _basal(conn, dev, start, end):
    rows = conn.execute(
        "SELECT time, minutes_of_day, insulin_units::float8 AS insulin_units, confirmed FROM basal_timing"
        " WHERE device_id = %s AND time >= %s AND time <= %s ORDER BY time", (dev, start, end)).fetchall()
    return {"rows": rows, "usual_time": None,
            "note": "the usual basal time is a device setting and the clean-night rise needs reason codes;"
                    " neither is forwarded to the cloud"}


def _sensor(conn, dev, start, end):
    first = conn.execute("SELECT min(time) AS t FROM readings WHERE device_id = %s", (dev,)).fetchone()["t"]
    s0 = datetime.combine(max(start, first).date(), datetime.min.time())   # days before the device existed are not gaps
    s1 = datetime.combine(end.date() + timedelta(days=1), datetime.min.time())
    rows = conn.execute(
        "WITH b AS ("
        "  SELECT time_bucket_gapfill(INTERVAL '5 minutes', time, %s::timestamp, %s::timestamp) AS slot,"
        "         count(time) AS n"
        "  FROM readings WHERE device_id = %s AND time >= %s AND time < %s GROUP BY slot)"
        " SELECT slot::date AS day, count(*) FILTER (WHERE coalesce(n, 0) > 0)::int AS slots_with_reading,"
        "        (5 * count(*) FILTER (WHERE coalesce(n, 0) = 0))::int AS gap_minutes"
        " FROM b GROUP BY 1 ORDER BY 1", (s0, s1, dev, s0, s1)).fetchall()
    for r in rows:
        r["coverage_pct"] = 100.0 * r["slots_with_reading"] / 288
    return {"rows": rows, "note": "the newest day is partial until it ends"}


def _under_the_hood(conn, dev, start, end):
    counts = conn.execute(
        "SELECT count(*)::int AS readings, min(time) AS first_reading, max(time) AS last_reading"
        " FROM readings WHERE device_id = %s", (dev,)).fetchone()
    comp = conn.execute(
        "SELECT sum(before_compression_total_bytes)::bigint AS before_bytes,"
        " sum(after_compression_total_bytes)::bigint AS after_bytes FROM hypertable_compression_stats('readings')"
    ).fetchone()
    size = conn.execute("SELECT hypertable_size('readings')::bigint AS total_bytes").fetchone()
    refresh = conn.execute(
        "SELECT max(s.last_successful_finish) AS last_aggregate_refresh"
        " FROM timescaledb_information.job_stats s JOIN timescaledb_information.jobs j USING (job_id)"
        " WHERE j.proc_name = 'policy_refresh_continuous_aggregate'").fetchone()
    before, after = comp["before_bytes"], comp["after_bytes"]
    return {**counts, "readings_table_bytes_all_devices": size["total_bytes"],
            "compressed_before_bytes": before, "compressed_after_bytes": after,
            "compression_ratio": (before / after) if before and after else None,
            "last_sync": counts["last_reading"], **refresh}


def _step_watch(conn, dev, start, end):
    plan = conn.execute(
        "SELECT plan_id, drug_class, drug_label, status, started_at, steps FROM plans"
        " WHERE device_id = %s AND status <> 'pending_confirm' ORDER BY started_at DESC, plan_id DESC LIMIT 1",
        (dev,)).fetchone()
    if plan is None:
        return {"plan": None, "baseline": None, "rows": [], "checkins": [], "injections": []}
    b0, b1 = plan["started_at"] - timedelta(days=BASELINE_NIGHTS), plan["started_at"] - timedelta(days=1)
    base = conn.execute(   # the median of non-stale low points, as nights.step_window_metrics
        "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY low_point_mgdl) AS low_point_mgdl, count(*)::int AS nights"
        " FROM night_records WHERE device_id = %s AND night_date >= %s AND night_date <= %s"
        " AND low_point_mgdl IS NOT NULL AND coverage_pct >= %s AND NOT ('stale' = ANY(reason_codes))",
        (dev, b0, b1, COVERAGE_OK_PCT)).fetchone()
    rows = conn.execute(
        "SELECT night_date, coverage_pct::float8 AS coverage_pct, low_point_mgdl::float8 AS low_point_mgdl,"
        " minutes_below_70, tbr_pct::float8 AS tbr_pct, near_miss_count, level2_count, reason_codes, code_source"
        " FROM night_records WHERE device_id = %s AND night_date >= %s AND night_date <= %s ORDER BY night_date",
        (dev, start.date(), end.date())).fetchall()
    ref = base["low_point_mgdl"]
    for r in rows:
        lp = r["low_point_mgdl"]
        r["vs_baseline_mgdl"] = lp - ref if lp is not None and ref is not None else None
    checkins = conn.execute(
        "SELECT date, gi FROM symptom_checks WHERE device_id = %s AND date >= %s AND date <= %s ORDER BY date",
        (dev, start.date(), end.date())).fetchall()
    shots = conn.execute(
        "SELECT time, dose_label, confirmed FROM treatments"
        " WHERE device_id = %s AND kind = 'glp1_dose' AND time >= %s AND time <= %s ORDER BY time",
        (dev, start, end)).fetchall()
    return {"plan": plan, "baseline": {"from": b0, "to": b1, **base}, "rows": rows,
            "checkins": checkins, "injections": shots}


def _buddy(conn, dev, start, end):
    rows = conn.execute(
        "SELECT time_bucket(INTERVAL '7 days', at) AS week,"
        " count(*) FILTER (WHERE kind = 'alert')::int AS alerts,"
        " count(*) FILTER (WHERE kind = 'claim')::int AS claims,"
        " count(*) FILTER (WHERE kind = 'call')::int AS calls,"
        " count(*) FILTER (WHERE kind = 'treating')::int AS treating,"
        " count(*) FILTER (WHERE kind = 'resolved')::int AS resolved,"
        " count(*) FILTER (WHERE kind = 'alert' AND confidence = 'device_confirmed')::int AS alerts_device_confirmed,"
        " count(*) FILTER (WHERE kind = 'alert' AND confidence = 'unconfirmed')::int AS alerts_unconfirmed"
        " FROM buddy_events WHERE device_id = %s AND at >= %s AND at <= %s GROUP BY 1 ORDER BY 1",
        (dev, start - timedelta(days=7), end)).fetchall()
    return {"rows": rows}


HANDLERS = {"nights": _nights, "tir": _tir, "profile": _profile, "lows_heatmap": _lows_heatmap,
            "alarms": _alarms, "near_misses": _near_misses, "basal": _basal, "sensor": _sensor,
            "under_the_hood": _under_the_hood, "step_watch": _step_watch, "buddy": _buddy}


def query(name: str, days: int, device_id: str, is_demo: bool = False) -> dict:
    days = max(1, min(int(days), MAX_DAYS))
    base = {"name": name, "device_id": device_id, "is_demo": is_demo, "days": days}
    with _connect() as conn:
        end = conn.execute("SELECT max(time) AS t FROM readings WHERE device_id = %s", (device_id,)).fetchone()["t"]
        if end is None:
            return {**base, "as_of": None, "available": True, "empty": True, "rows": []}
        start = end - timedelta(days=days)
        out = HANDLERS[name](conn, device_id, start, end)
    empty = "rows" in out and not out["rows"]
    return {**base, "as_of": end, "available": True, "empty": empty, **out}


# ---------------------------------------------------------------- family rollup (C3)

def family_last_night(device_id: str, is_demo: bool = False) -> dict:
    """The newest reading (with minutes since it and a stale flag against the
    device's local clock) and the newest night: its low, high, minutes under
    70, coverage, and the strip of readings. Never a decision (invariant 21)."""
    now = device_now()
    with _connect() as conn:
        cur = conn.execute("SELECT time, mgdl::float8 AS mgdl, trend FROM readings WHERE device_id = %s"
                           " ORDER BY time DESC LIMIT 1", (device_id,)).fetchone()
        night = conn.execute(
            "SELECT night_date, coverage_pct::float8 AS coverage_pct, minutes_below_70, readings FROM nightly"
            " WHERE device_id = %s ORDER BY night_date DESC LIMIT 1", (device_id,)).fetchone()
        strip, low, high = [], None, None
        if night:
            n0 = datetime.combine(night["night_date"], datetime.min.time()) + timedelta(hours=NIGHT_START_H)
            n1 = n0 + timedelta(hours=NIGHT_HOURS)
            strip = conn.execute("SELECT time, mgdl::float8 AS mgdl FROM readings WHERE device_id = %s"
                                 " AND time >= %s AND time < %s ORDER BY time", (device_id, n0, n1)).fetchall()
            if strip:
                lo = min(strip, key=lambda r: r["mgdl"])
                hi = max(strip, key=lambda r: r["mgdl"])
                low = {"mgdl": lo["mgdl"], "at": lo["time"]}
                high = {"mgdl": hi["mgdl"], "at": hi["time"]}
    current = None
    if cur:
        minutes = (now - cur["time"]).total_seconds() / 60
        current = {"mgdl": cur["mgdl"], "trend": cur["trend"], "at": cur["time"],
                   "minutes_since": round(minutes, 1), "stale": minutes > STALE_MIN}
    last_night = None
    if night:
        n1 = datetime.combine(night["night_date"], datetime.min.time()) + timedelta(hours=NIGHT_START_H + NIGHT_HOURS)
        last_night = {"night_date": night["night_date"], "in_progress": now < n1, "low": low, "high": high,
                      "minutes_below_70": night["minutes_below_70"], "coverage_pct": night["coverage_pct"],
                      "strip": strip}
    return {"device_id": device_id, "is_demo": is_demo, "as_of": now, "current": current, "last_night": last_night}
