"""C3 check: every dashboard renders JSON over seeded nights; the owner
bearer and the family bearers gate what they should (a revoked family bearer
gets 401); demo reads the -demo device; the nights dashboard equals
ml/models/nights.py (invariant 21). Runs in a throwaway schema on TIGER_URI
(the compose timescaledb, or a Tiger Cloud scratch schema), with cloud/sql
applied exactly as migrate.py does and committed demo scenarios as data
(SYNTHETIC or date-shifted; never real device rows). Skips when unreachable.
step_watch reads the SYNTHETIC titration companion's plan, reason codes,
check-ins, and shots (005); its baseline equals nights.step_window_metrics."""

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
STALE_BASELINE_NIGHT = "2020-01-03"   # re-coded stale in the seed: the baseline must skip it
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


# SYNTHETIC buddy events: one device-confirmed alert that ran the whole ladder, one unconfirmed alert.
BUDDY_EVENTS = [
    ("e1", "alert", datetime(2020, 2, 10, 2, 30), "device_confirmed"),
    ("e1", "claim", datetime(2020, 2, 10, 2, 33), None),
    ("e1", "call", datetime(2020, 2, 10, 2, 34), None),
    ("e1", "treating", datetime(2020, 2, 10, 2, 36), None),
    ("e1", "resolved", datetime(2020, 2, 10, 2, 55), None),
    ("e2", "alert", datetime(2020, 2, 17, 3, 0), "unconfirmed"),
]


def night_records(comp):
    """NightRecords as the Pi's ledger would forward them: nights.py numbers + the companion's codes."""
    from ml.models import nights as N

    readings = [{"timestamp": t, "glucose_mgdl": v} for t, v, _ in scenario("titration_synthetic")]
    out = []
    for day, rc in comp["reason_codes"].items():
        w0 = datetime.combine(datetime.fromisoformat(day).date(), time(22))
        m = N.night_metrics(readings, None, (w0, w0 + timedelta(hours=9)))
        out.append({"night_date": datetime.fromisoformat(day).date(), "window_start": w0,
                    "window_end": w0 + timedelta(hours=9), "coverage_pct": m["coverage_pct"],
                    "reason_codes": ["stale"] if day == STALE_BASELINE_NIGHT else rc["codes"],
                    "code_source": rc["code_source"], **{k: m[k] for k in (
                        "rise_mgdl", "low_point_mgdl", "tbr_pct", "minutes_below_70", "near_miss_count", "level2_count")}})
    return out


@pytest.fixture(scope="module")
def seeded():
    import psycopg
    from psycopg.types.json import Jsonb

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
            for night in night_records(comp):
                cur.execute("INSERT INTO night_records (device_id, night_date, window_start, window_end, coverage_pct,"
                            " reason_codes, code_source, rise_mgdl, low_point_mgdl, tbr_pct, minutes_below_70,"
                            " near_miss_count, level2_count, is_demo)"
                            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, false)",
                            (DEV, night["night_date"], night["window_start"], night["window_end"], night["coverage_pct"],
                             night["reason_codes"], night["code_source"], night["rise_mgdl"], night["low_point_mgdl"],
                             night["tbr_pct"], night["minutes_below_70"], night["near_miss_count"], night["level2_count"]))
            plan = comp["plan"]
            for pid, started, status in ((plan["plan_id"], plan["started_at"], "active"),
                                         ("not-yet-confirmed", "2020-02-01", "pending_confirm")):
                cur.execute("INSERT INTO plans (device_id, started_at, plan_id, drug_class, drug_label, steps, status, is_demo)"
                            " VALUES (%s, %s, %s, %s, %s, %s, %s, false)",
                            (DEV, started, pid, plan["drug_class"], plan["drug_label"], Jsonb(plan["steps"]), status))
            for c in comp["symptom_checks"]:
                cur.execute("INSERT INTO symptom_checks (device_id, date, gi, is_demo) VALUES (%s, %s, %s, false)",
                            (DEV, c["date"], c["gi"]))
            for i in comp["injections"]:
                cur.execute("INSERT INTO treatments (device_id, time, kind, dose_label, confirmed, is_demo)"
                            " VALUES (%s, %s, 'glp1_dose', %s, %s, false)",
                            (DEV, i["timestamp"], i["dose_label"], i["confirmed"]))
            for eid, kind, at, conf in BUDDY_EVENTS:
                cur.execute("INSERT INTO buddy_events (device_id, at, event_id, kind, confidence, is_demo)"
                            " VALUES (%s, %s, %s, %s, %s, false)", (DEV, at, eid, kind, conf))
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
    assert sensor[0]["day"] == "2020-01-01"                          # starts at the first reading, not 60 days of fake gaps


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


def test_step_watch_draws_the_confirmed_plan_against_nights_py(client):
    """Invariant 21 at the API: the baseline line is nights.step_window_metrics' baseline_low_point."""
    from ml.models import nights as N

    comp = json.loads((ROOT / "demo" / "scenarios" / "titration_synthetic.json").read_text())
    body = client.get("/v1/dash/step_watch?days=60", headers=OWNER).json()
    assert body["plan"]["plan_id"] == comp["plan"]["plan_id"]          # never the pending_confirm plan
    assert body["plan"]["steps"] == comp["plan"]["steps"] and body["plan"]["status"] == "active"
    base = body["baseline"]
    assert (base["from"], base["to"]) == ("2020-01-01", "2020-01-14")
    records = night_records(comp)
    baseline_records = [r for r in records if r["night_date"].isoformat() <= "2020-01-14"]
    values, _ = N.step_window_metrics(records, baseline_records, [], [])
    assert base["low_point_mgdl"] == pytest.approx(values["baseline_low_point"], abs=1e-4)   # real columns
    assert base["nights"] == values["baseline_nights"] == 13                # the stale night is skipped
    night = {r["night_date"]: r for r in body["rows"]}
    assert night[STALE_BASELINE_NIGHT]["reason_codes"] == ["stale"]
    some = next(r for r in body["rows"] if r["low_point_mgdl"] is not None)
    assert some["vs_baseline_mgdl"] == pytest.approx(some["low_point_mgdl"] - base["low_point_mgdl"])
    assert {r["code_source"] for r in body["rows"]} == {"inferred"}
    assert len(body["checkins"]) == len([c for c in comp["symptom_checks"] if c["date"] >= body["rows"][0]["night_date"]])
    assert body["injections"] and all(i["dose_label"] for i in body["injections"])
    assert body["empty"] is False


def test_buddy_counts_per_week_split_by_confidence(client):
    rows = client.get("/v1/dash/buddy?days=60", headers=OWNER).json()["rows"]
    assert sum(r["alerts"] for r in rows) == 2
    assert sum(r["alerts_device_confirmed"] for r in rows) == 1 and sum(r["alerts_unconfirmed"] for r in rows) == 1
    assert all(sum(r[k] for r in rows) == 1 for k in ("claims", "calls", "treating", "resolved"))
    assert len(rows) == 2


def test_no_plan_says_so(client, monkeypatch):
    monkeypatch.setattr(main, "DEVICE_ID", f"{DEV}-demo")              # readings, but no plan forwarded
    body = client.get("/v1/dash/step_watch", headers=OWNER).json()
    assert body["plan"] is None and body["baseline"] is None and body["empty"] is True and body["rows"] == []


def test_forwarded_rows_draw_through_ingest(client, seeded, monkeypatch):
    """The seam end to end: what ingest.py writes into the migrated 005 tables (hypertables
    included) is what step_watch and buddy draw; demo rows only on the -demo device."""
    import ingest as ingest_mod

    monkeypatch.setattr(ingest_mod, "TIGER_URI", seeded)
    monkeypatch.setattr(main, "DEVICE_TOKEN", "tok-e2e")
    w0 = datetime(2021, 3, 1, 22)
    batch = {
        "device_id": DEV,
        "night_records": [{"night_date": "2021-03-01", "window_start": w0.isoformat(),
                           "window_end": (w0 + timedelta(hours=9)).isoformat(), "coverage_pct": 96.0,
                           "reason_codes": ["clean"], "code_source": "logged", "low_point_mgdl": 84.0, "is_demo": True}],
        "plans": [{"plan_id": "e2e-plan", "drug_class": "glp1", "drug_label": "semaglutide", "started_at": "2021-02-20",
                   "steps": [{"index": 0, "dose_label": "0.25 mg", "planned_start": "2021-02-20"}],
                   "status": "active", "is_demo": True}],
        "symptom_checks": [{"date": "2021-03-01", "gi": "rough", "is_demo": True}],
        "buddy_events": [{"event_id": "e2e", "kind": "alert", "at": "2021-02-28T03:00:00", "confidence": "unconfirmed",
                          "is_demo": True}],
    }
    r = client.post("/v1/ingest", json=batch, headers={"X-Device-Id": DEV, "X-Device-Token": "tok-e2e"})
    assert r.status_code == 200, r.text
    sw = client.get("/v1/dash/step_watch?demo=true&days=30", headers=OWNER).json()
    assert sw["device_id"] == f"{DEV}-demo" and sw["plan"]["plan_id"] == "e2e-plan"
    assert [(n["night_date"], n["reason_codes"], n["code_source"]) for n in sw["rows"]] == [("2021-03-01", ["clean"], "logged")]
    assert sw["checkins"] == [{"date": "2021-03-01", "gi": "rough"}]
    bd = client.get("/v1/dash/buddy?demo=true&days=30", headers=OWNER).json()
    assert sum(w["alerts_unconfirmed"] for w in bd["rows"]) == 1
    real = client.get("/v1/dash/step_watch?days=60", headers=OWNER).json()
    assert real["plan"]["plan_id"] != "e2e-plan"                       # demo never drawn as the real device


def test_refresh_stops_behind_the_devices_local_clock(seeded):
    """006: timestamps are naive local (UTC-4/-5) but end_offset counts from UTC now(); every
    aggregate's policy must stop at least 6 h back so the real-time union draws fresh readings."""
    import psycopg

    schema = seeded.split("search_path%3D", 1)[1].split("%2C", 1)[0]
    with psycopg.connect(seeded) as conn:
        rows = conn.execute(
            "SELECT hypertable_name, (config->>'end_offset')::interval FROM timescaledb_information.jobs"
            " WHERE hypertable_schema = %s AND proc_name = 'policy_refresh_continuous_aggregate'", (schema,)).fetchall()
    assert {r[0] for r in rows} == {"daily_stats", "overnight_profile", "hourly_heatmap", "alarms_weekly"}
    assert all(r[1] >= timedelta(hours=6) for r in rows), rows
