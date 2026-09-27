"""C1 check: the same batch sent twice counts one row per reading; a bad
device token gets 401; demo rows land under <device_id>-demo. Needs a
reachable TIGER_URI (compose timescaledb, or a Tiger Cloud scratch schema);
the tables are created as plain tables in a throwaway schema."""

import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ingest as ingest_mod  # noqa: E402
import main  # noqa: E402
from main import app  # noqa: E402

DDL = """
CREATE TABLE readings (device_id text NOT NULL, time timestamp NOT NULL, mgdl real NOT NULL, trend text,
  source text, is_demo bool NOT NULL DEFAULT false, PRIMARY KEY (device_id, time));
CREATE TABLE alarm_events (device_id text NOT NULL, started_at timestamp NOT NULL, event_id text NOT NULL,
  tier text NOT NULL, acknowledged_at timestamp, ack_source text, escalated bool, rearm_count int,
  crossed_actual bool, presence_during text, is_demo bool NOT NULL DEFAULT false,
  PRIMARY KEY (device_id, started_at, event_id));
CREATE TABLE low_events (device_id text NOT NULL, started_at timestamp NOT NULL, low_event_id text NOT NULL,
  night_date date, nadir_mgdl real, nadir_at timestamp, minutes_below_70 int, auc_below_70 real,
  recovery_slope real, carbs_logged_within_30min bool, inferred_unfelt bool, alarm_event_id text,
  is_demo bool NOT NULL DEFAULT false, PRIMARY KEY (device_id, started_at, low_event_id));
CREATE TABLE treatments (device_id text NOT NULL, time timestamp NOT NULL, kind text NOT NULL,
  insulin_units real, carbs_g real, dose_label text, text text, confirmed bool,
  is_demo bool NOT NULL DEFAULT false, PRIMARY KEY (device_id, time, kind));
"""

H = {"X-Device-Id": "irin-test-0001", "X-Device-Token": "tok-1234"}


def test_health():
    with TestClient(app) as c:
        r = c.get("/v1/health")
        assert r.status_code == 200 and r.json()["ok"] is True


@pytest.fixture
def scratch(monkeypatch):
    import psycopg

    uri = os.environ.get("TIGER_URI", ingest_mod.TIGER_URI)
    schema = f"ingest_test_{uuid.uuid4().hex[:8]}"
    try:
        conn = psycopg.connect(uri, connect_timeout=3)
    except Exception as e:  # no database here: the check needs the compose timescaledb
        pytest.skip(f"TIGER_URI unreachable ({type(e).__name__})")
    with conn:
        conn.execute(f"CREATE SCHEMA {schema}")
        conn.execute(f"SET search_path TO {schema}")
        conn.execute(DDL)
    conn.close()
    sep = "&" if "?" in uri else "?"
    scoped = f"{uri}{sep}options=-c%20search_path%3D{schema}"
    monkeypatch.setattr(ingest_mod, "TIGER_URI", scoped)
    monkeypatch.setattr(main, "DEVICE_ID", "irin-test-0001")
    monkeypatch.setattr(main, "DEVICE_TOKEN", "tok-1234")
    yield scoped
    with psycopg.connect(uri) as conn:
        conn.execute(f"DROP SCHEMA {schema} CASCADE")


def count(uri, table, device_id=None):
    import psycopg

    with psycopg.connect(uri) as conn:
        if device_id is None:
            return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        return conn.execute(f"SELECT count(*) FROM {table} WHERE device_id = %s", (device_id,)).fetchone()[0]


def batch(n=3, demo=False):
    t0 = datetime(2021, 3, 1, 0, 0)
    return {
        "device_id": "irin-test-0001",
        "readings": [{"timestamp": t0.replace(minute=5 * i).isoformat(), "glucose_mgdl": 100 + i, "trend": "Flat",
                      "source": "replay" if demo else "nightscout", "is_stale": False, "is_demo": demo} for i in range(n)],
        "alarm_events": [{"event_id": "ev-1", "tier": "actual_low", "started_at": t0.isoformat(), "escalated": True,
                          "presence_during": "home", "is_demo": demo}],
        "low_events": [{"low_event_id": "low-1", "night_date": "2021-03-01", "started_at": t0.isoformat(), "nadir_mgdl": 52,
                        "nadir_at": t0.isoformat(), "minutes_below_70": 20, "auc_below_70": 180.0, "is_demo": demo}],
        "treatments": [{"timestamp": t0.isoformat(), "kind": "carbs", "carbs_g": 15, "confirmed": True, "is_demo": demo}],
    }


def test_same_batch_twice_stores_one_row_per_reading(scratch):
    with TestClient(app) as c:
        r = c.post("/v1/ingest", json=batch(), headers=H)
        assert r.status_code == 200 and r.json()["stored"]["readings"] == 3
        r = c.post("/v1/ingest", json=batch(), headers=H)  # the Pi retries after a lost 200
        assert r.status_code == 200
    assert count(scratch, "readings") == 3
    assert count(scratch, "alarm_events") == 1 and count(scratch, "low_events") == 1 and count(scratch, "treatments") == 1


def test_demo_rows_land_under_the_demo_device_id(scratch):
    with TestClient(app) as c:
        assert c.post("/v1/ingest", json=batch(demo=True), headers=H).status_code == 200
        assert c.post("/v1/ingest", json=batch(n=2), headers=H).status_code == 200
    assert count(scratch, "readings", "irin-test-0001-demo") == 3
    assert count(scratch, "readings", "irin-test-0001") == 2
    assert count(scratch, "alarm_events", "irin-test-0001-demo") == 1


def test_bad_token_or_mismatched_device_is_rejected(scratch):
    with TestClient(app) as c:
        assert c.post("/v1/ingest", json=batch()).status_code == 401
        assert c.post("/v1/ingest", json=batch(), headers={**H, "X-Device-Token": "wrong"}).status_code == 401
        assert c.post("/v1/ingest", json={**batch(), "device_id": "other"}, headers=H).status_code == 400
        assert c.post("/v1/ingest", json={**batch(), "device_id": "../x"}, headers=H).status_code == 422
    assert count(scratch, "readings") == 0


def test_unreachable_storage_is_503_so_the_pi_keeps_its_cursor(monkeypatch):
    monkeypatch.setattr(main, "DEVICE_ID", "irin-test-0001")
    monkeypatch.setattr(main, "DEVICE_TOKEN", "tok-1234")
    monkeypatch.setattr(ingest_mod, "TIGER_URI", "postgresql://postgres:postgres@127.0.0.1:1/irin?connect_timeout=1")
    with TestClient(app) as c:
        assert c.post("/v1/ingest", json=batch(), headers=H).status_code == 503


def test_rounds_and_buddy_rows_upsert_under_the_right_device(scratch):
    """C5: night_records, plans, symptom_checks and buddy_events from cloud/sql/005 (George's
    column contract); a re-sent night replaces the stored one; demo rows under <device>-demo."""
    import pathlib
    import re

    import psycopg

    sql = (pathlib.Path(__file__).resolve().parents[1] / "sql" / "005_rounds_buddy.sql").read_text()
    sql = re.sub(r"(?m)^SELECT create_hypertable.*$", "", sql)  # plain tables are enough for the contract
    with psycopg.connect(scratch) as conn:
        conn.execute(sql)
    body = {"device_id": "irin-test-0001",
            "night_records": [{"night_date": "2020-01-01", "window_start": "2020-01-01T22:00:00", "window_end": "2020-01-02T07:00:00",
                               "coverage_pct": 95.0, "reason_codes": ["clean"], "code_source": "inferred", "low_point_mgdl": 98.0,
                               "is_demo": True}],
            "plans": [{"plan_id": "p1", "drug_class": "gip_glp1", "drug_label": "tirzepatide", "started_at": "2020-01-15",
                       "steps": [{"index": 0, "dose_label": "2.5 mg", "planned_start": "2020-01-15"}], "status": "active", "is_demo": True}],
            "symptom_checks": [{"date": "2020-01-16", "gi": "rough", "is_demo": True}],
            "buddy_events": [{"event_id": "ba-1", "kind": "alert", "at": "2020-01-16T03:10:00", "confidence": "device_confirmed", "is_demo": True}]}
    h = {"X-Device-Id": "irin-test-0001", "X-Device-Token": "tok-1234"}
    with TestClient(app) as c:
        r = c.post("/v1/ingest", json=body, headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["stored"]["night_records"] == 1 and r.json()["stored"]["buddy_events"] == 1
        body["night_records"][0]["reason_codes"] = ["late_meal"]
        assert c.post("/v1/ingest", json=body, headers=h).status_code == 200
    for table in ("night_records", "plans", "symptom_checks", "buddy_events"):
        assert count(scratch, table, "irin-test-0001-demo") == 1 and count(scratch, table, "irin-test-0001") == 0, table
    with psycopg.connect(scratch) as conn:
        assert conn.execute("SELECT reason_codes FROM night_records").fetchone()[0] == ["late_meal"]
        assert conn.execute("SELECT steps->0->>'dose_label' FROM plans").fetchone()[0] == "2.5 mg"
