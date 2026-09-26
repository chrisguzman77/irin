"""C3 check: every dashboard renders JSON over seeded nights; the owner
bearer and the family bearers gate what they should (a revoked family bearer
gets 401); demo reads the -demo device; the nights dashboard equals
ml/models/nights.py (invariant 21). Runs in a throwaway schema on TIGER_URI
(the compose timescaledb, or a Tiger Cloud scratch schema), with cloud/sql
applied exactly as migrate.py does and committed demo scenarios as data
(SYNTHETIC or date-shifted; never real device rows). Skips when unreachable."""

import csv
import json
import os
import sys
import uuid
from datetime import datetime, time, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

CLOUD = Path(__file__).resolve().parents[1]
ROOT = CLOUD.parent
sys.path.insert(0, str(CLOUD))
sys.path.insert(0, str(ROOT))
import dash as dash_mod  # noqa: E402
import main  # noqa: E402
from main import DASH_NAMES, app  # noqa: E402

DEV = "c3-test-dev"
OWNER = {"Authorization": "Bearer owner-test-token"}
PIN = {"X-PIN": "4321"}


def tiger_uri():
    uri = os.environ.get("TIGER_URI")
    env = ROOT / ".env"
    if not uri and env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("TIGER_URI="):
                uri = line.split("=", 1)[1].strip().strip('"').strip("'")
    return uri


def scenario(name):
    with (ROOT / "demo" / "scenarios" / f"{name}.csv").open() as f:
        return [(datetime.fromisoformat(r["timestamp"]), float(r["glucose_mgdl"]), r["trend"]) for r in csv.DictReader(f)]


@pytest.fixture(scope="module")
def seeded():
    import psycopg

    uri = tiger_uri()
    if not uri:
        pytest.skip("TIGER_URI not set")
    try:
        conn = psycopg.connect(uri, connect_timeout=8, autocommit=True)
    except Exception as e:
        pytest.skip(f"TIGER_URI unreachable ({type(e).__name__})")
    schema = f"c3_test_{uuid.uuid4().hex[:8]}"
    conn.execute(f"CREATE SCHEMA {schema}")
    conn.execute(f"SET search_path TO {schema}, public")
    try:
        for path in sorted((CLOUD / "sql").glob("*.sql")):
            with conn.transaction():                              # as migrate.py: one transaction per file
                conn.execute(path.read_text())
        with conn.cursor() as cur:
            for dev, name in ((DEV, "titration_synthetic"), (f"{DEV}-demo", "the_save")):
                with cur.copy("COPY readings (device_id, time, mgdl, trend, source, is_demo) FROM STDIN") as cp:
                    for t, v, tr in scenario(name):
                        cp.write_row((dev, t, v, tr, "replay", dev.endswith("-demo")))
            comp = json.loads((ROOT / "demo" / "scenarios" / "titration_synthetic.json").read_text())
            for a in comp["alarm_events"]:
                cur.execute("INSERT INTO alarm_events (device_id, started_at, event_id, tier, acknowledged_at, ack_source,"
                            " escalated, rearm_count, crossed_actual, presence_during, is_demo)"
                            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                            (DEV, a["started_at"], a["event_id"], a["tier"], a["acknowledged_at"], a["ack_source"],
                             a["escalated"], a["rearm_count"], a["crossed_actual"], a["presence_during"], False))
            for d in range(40, 47):                               # a week of SYNTHETIC basal doses
                cur.execute("INSERT INTO treatments (device_id, time, kind, insulin_units, confirmed, is_demo)"
                            " VALUES (%s, %s, 'basal', 18, true, false)",
                            (DEV, datetime(2020, 1, 1, 21, 5 * (d % 3)) + timedelta(days=d)))
        sep = "&" if "?" in uri else "?"
        scoped = f"{uri}{sep}options=-c%20search_path%3D{schema}%2Cpublic"
        yield scoped
    finally:
        conn.execute("SET search_path TO public")
        conn.execute(f"DROP SCHEMA {schema} CASCADE")
        conn.close()


@pytest.fixture
def client(seeded, monkeypatch):
    monkeypatch.setattr(dash_mod, "TIGER_URI", seeded)
    monkeypatch.setattr(main, "DEVICE_ID", DEV)
    monkeypatch.setattr(main, "OWNER_BEARER", "owner-test-token")
    monkeypatch.setattr(main, "PIN", "4321")
    with TestClient(app) as c:
        yield c


def test_owner_bearer_gates_every_dashboard(client, monkeypatch):
    assert client.get("/v1/dash/nights").status_code == 401
    assert client.get("/v1/dash/nights", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/v1/dash/nights", headers={"Authorization": "owner-test-token"}).status_code == 401  # no scheme
    assert client.get("/v1/dash/nope", headers=OWNER).status_code == 404
    monkeypatch.setattr(main, "OWNER_BEARER", "")
    assert client.get("/v1/dash/nights", headers=OWNER).status_code == 503   # fail closed, never open


@pytest.mark.parametrize("name", DASH_NAMES)
def test_every_dashboard_renders(client, name):
    r = client.get(f"/v1/dash/{name}?days=60", headers=OWNER)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == name and body["device_id"] == DEV and body["is_demo"] is False and body["days"] == 60
    if name in ("step_watch", "buddy"):
        assert body["available"] is False and ("forwarded" in body["reason"] or "relay" in body["reason"])
        return
    assert body["available"] is True and body["as_of"].startswith("2020-02-19")   # the scenario's newest reading
    if name == "under_the_hood":
        assert body["readings"] > 14000 and body["last_sync"] == body["last_reading"]
    else:
        assert body["empty"] is False and body["rows"], name


def test_shapes_the_charts_draw(client):
    g = lambda n: client.get(f"/v1/dash/{n}?days=60", headers=OWNER).json()["rows"]  # noqa: E731
    prof = g("profile")
    assert len(prof) == 48 and all(p["p10"] <= p["p50"] <= p["p90"] for p in prof)
    tir = g("tir")
    assert all(abs(t["share_under_70"] + t["share_70_180"] + t["share_over_180"] - 1) < 1e-9 for t in tir)
    assert tir[-1]["in_range_7d"] is not None and tir[-1]["in_range_30d"] is not None
    heat = g("lows_heatmap")
    assert {h["weekday"] for h in heat} <= set(range(1, 8)) and sum(h["under_70"] for h in heat) > 0
    assert sum(w["near_misses"] for w in g("near_misses")) == 2       # the scenario's two near-miss warnings
    assert {a["tier"] for a in g("alarms")} == {"predicted_low", "actual_low"}
    assert len(g("basal")) == 7
    sensor = g("sensor")
    assert all(0 <= s["coverage_pct"] <= 100 for s in sensor) and any(s["gap_minutes"] > 0 for s in sensor)


def test_nights_dashboard_equals_nights_py(client):
    """Invariant 21 at the API: the numbers the nights strip draws are the ones the cards compute."""
    from ml.models import nights as N

    readings = [{"timestamp": t, "glucose_mgdl": v} for t, v, _ in scenario("titration_synthetic")]
    rows = client.get("/v1/dash/nights?days=60", headers=OWNER).json()["rows"]
    assert len(rows) >= 45
    for r in rows:
        d = datetime.fromisoformat(r["night_date"])
        m = N.night_metrics(readings, None, (datetime.combine(d, time(22)), datetime.combine(d, time(22)) + timedelta(hours=9)))
        assert r["readings"] == m["readings"] and r["minutes_below_70"] == m["minutes_below_70"]
        assert r["coverage_pct"] == pytest.approx(m["coverage_pct"], abs=1e-9)
        assert r["low_point_mgdl"] == pytest.approx(m["low_point_mgdl"], abs=1e-9)


def test_demo_reads_the_demo_device(client):
    body = client.get("/v1/dash/nights?demo=true", headers=OWNER).json()
    assert body["device_id"] == f"{DEV}-demo" and body["is_demo"] is True
    assert body["as_of"].startswith("2021-03-01")                    # the_save, never the real device


def test_empty_device_says_so(client, monkeypatch):
    monkeypatch.setattr(main, "DEVICE_ID", "c3-nobody")
    body = client.get("/v1/dash/tir", headers=OWNER).json()
    assert body["empty"] is True and body["rows"] == [] and body["as_of"] is None


def test_family_bearer_lifecycle(client):
    assert client.post("/v1/family/bearers", json={"recipient_id": "mom"}).status_code == 401          # no PIN
    assert client.post("/v1/family/bearers", json={"recipient_id": "mom"},
                       headers={"X-PIN": "0000"}).status_code == 401
    created = client.post("/v1/family/bearers", json={"recipient_id": "mom"}, headers=PIN).json()
    token = created["token"]
    assert created["recipient_id"] == "mom" and created["is_demo"] is False and len(token) > 30
    fam = {"Authorization": f"Bearer {token}"}

    r = client.get("/v1/family/last_night", headers=fam)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"device_id", "is_demo", "as_of", "current", "last_night"}
    assert set(body["current"]) == {"mgdl", "trend", "at", "minutes_since", "stale"}
    assert body["current"]["stale"] is True                          # 2020 data is honestly stale today
    ln = body["last_night"]
    assert set(ln) == {"night_date", "in_progress", "low", "high", "minutes_below_70", "coverage_pct", "strip"}
    assert ln["strip"] and ln["low"]["mgdl"] <= ln["high"]["mgdl"]

    assert client.get("/v1/family/last_night").status_code == 401
    assert client.get("/v1/family/last_night", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/v1/dash/nights", headers=fam).status_code == 401   # a family bearer is not the owner's

    assert client.delete(f"/v1/family/bearers?bearer_id={created['bearer_id']}", headers=PIN).json()["revoked"] is True
    assert client.get("/v1/family/last_night", headers=fam).status_code == 401   # revoked: refused at once
    assert client.delete(f"/v1/family/bearers?bearer_id={created['bearer_id']}", headers=PIN).status_code == 404


def test_family_demo_bearer_reads_the_demo_device(client):
    token = client.post("/v1/family/bearers", json={"recipient_id": "demo-mom", "demo": True}, headers=PIN).json()["token"]
    body = client.get("/v1/family/last_night", headers={"Authorization": f"Bearer {token}"}).json()
    assert body["is_demo"] is True and body["device_id"] == f"{DEV}-demo"
