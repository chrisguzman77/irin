"""george.md step 9.5 / invariant 21: where the dashboards' SQL and
ml/models/nights.py compute the same number, they agree, to the reading.

Creates a scratch schema on TIGER_URI (Tiger Cloud, or Chris's compose
timescaledb), applies cloud/sql/*.sql there exactly as migrate.py does (one
transaction per file), loads committed demo scenarios (SYNTHETIC or
date-shifted; never raw data), and compares the `nightly` view with
nights.night_metrics on coverage, low point, and minutes under 70 for every
night. Drops the schema afterwards. Skips when psycopg or TIGER_URI is
missing or the service is unreachable."""

from __future__ import annotations

import csv
import os
import uuid
from datetime import datetime, time, timedelta
from pathlib import Path

import pytest

from ml.models import nights as N

ROOT = Path(__file__).resolve().parents[2]
SQL = sorted((ROOT / "cloud" / "sql").glob("*.sql"))
SCENARIOS = ["titration_synthetic", "the_save", "failure"]
DEVICE = "agreement-test"


def tiger_uri() -> str | None:
    uri = os.environ.get("TIGER_URI")
    env = ROOT / ".env"
    if not uri and env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("TIGER_URI="):
                uri = line.split("=", 1)[1].strip().strip('"').strip("'")
    return uri or None


@pytest.fixture(scope="module")
def db():
    psycopg = pytest.importorskip("psycopg")
    uri = tiger_uri()
    if not uri:
        pytest.skip("TIGER_URI not set")
    try:
        conn = psycopg.connect(uri, connect_timeout=10, autocommit=True)
    except Exception as e:  # unreachable service: skip, never fail the suite
        pytest.skip(f"Tiger unreachable: {type(e).__name__}")
    schema = f"agree_{uuid.uuid4().hex[:10]}"
    conn.execute(f"CREATE SCHEMA {schema}")
    conn.execute(f"SET search_path TO {schema}, public")
    try:
        for path in SQL:
            with conn.transaction():                  # as migrate.py: one transaction per file
                conn.execute(path.read_text())
        yield conn
    finally:
        conn.execute("SET search_path TO public")
        conn.execute(f"DROP SCHEMA {schema} CASCADE")
        conn.close()


def readings_of(name):
    with (ROOT / "demo" / "scenarios" / f"{name}.csv").open() as f:
        return [{"timestamp": datetime.fromisoformat(r["timestamp"]), "glucose_mgdl": float(r["glucose_mgdl"]),
                 "trend": r["trend"]} for r in csv.DictReader(f)]


@pytest.fixture(scope="module")
def loaded(db):
    per = {}
    with db.cursor() as cur:
        for name in SCENARIOS:
            rows = readings_of(name)
            device = f"{DEVICE}-{name}"
            with cur.copy("COPY readings (device_id, time, mgdl, trend, source, is_demo) FROM STDIN") as cp:
                for r in rows:
                    cp.write_row((device, r["timestamp"], r["glucose_mgdl"], r["trend"], "replay", True))
            per[name] = (device, rows)
    return per


def test_schema_matches_the_ingest_column_contract(db):
    cols = {t: [r[0] for r in db.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = %s"
        " ORDER BY ordinal_position", (t,))] for t in ("readings", "alarm_events", "low_events", "treatments")}
    assert cols["readings"] == ["device_id", "time", "mgdl", "trend", "source", "is_demo"]
    assert cols["alarm_events"][:3] == ["device_id", "started_at", "event_id"]
    assert cols["low_events"][:3] == ["device_id", "started_at", "low_event_id"]
    assert cols["treatments"][:3] == ["device_id", "time", "kind"]
    hyper = {r[0] for r in db.execute(
        "SELECT hypertable_name FROM timescaledb_information.hypertables WHERE hypertable_schema = current_schema()")}
    assert hyper == {"readings", "alarm_events", "low_events", "treatments"}
    caggs = {r[0] for r in db.execute(
        "SELECT view_name FROM timescaledb_information.continuous_aggregates WHERE view_schema = current_schema()")}
    assert caggs == {"daily_stats", "overnight_profile", "hourly_heatmap", "alarms_weekly"}


def test_ingest_upsert_targets_exist(db):
    # the same statement shape cloud/ingest.py uses: a retry stores one row
    for _ in range(2):
        db.execute("INSERT INTO readings (device_id, time, mgdl, trend, source, is_demo) VALUES (%s, %s, %s, %s, %s, %s)"
                   " ON CONFLICT (device_id, time) DO UPDATE SET mgdl = EXCLUDED.mgdl",
                   ("upsert-probe", datetime(2020, 1, 1, 3), 100.0, "Flat", "replay", True))
    assert db.execute("SELECT count(*) FROM readings WHERE device_id = 'upsert-probe'").fetchone()[0] == 1


def test_nightly_view_agrees_with_nights_py_to_the_reading(db, loaded):
    compared = 0
    for name, (device, rows) in loaded.items():
        sql = {r[0]: r[1:] for r in db.execute(
            "SELECT night_date, readings, coverage_pct, low_point_mgdl, minutes_below_70 FROM nightly"
            " WHERE device_id = %s ORDER BY night_date", (device,))}
        assert sql, name
        for night_date, (n_sql, cov_sql, low_sql, min_sql) in sql.items():
            start = datetime.combine(night_date, time(22, 0))
            m = N.night_metrics(rows, None, (start, start + timedelta(hours=9)))
            assert n_sql == m["readings"], (name, night_date)
            assert float(cov_sql) == pytest.approx(m["coverage_pct"], abs=1e-9), (name, night_date)
            assert min_sql == m["minutes_below_70"], (name, night_date)
            if m["low_point_mgdl"] is None:
                assert low_sql is None, (name, night_date)
            else:
                assert float(low_sql) == pytest.approx(m["low_point_mgdl"], abs=1e-9), (name, night_date)
            compared += 1
    assert compared >= 50                                 # 49+ titration nights, the Save, the failure night
