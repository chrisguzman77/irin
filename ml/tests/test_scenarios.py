"""george.md step 7: the SYNTHETIC titration scenario reproduces the chris.md
R10 worked example EXACTLY through ml/models/nights.py, its companion JSON
validates against backend/app/contracts.py, and the CSV loads the way the
replay datasource reads it. Regenerating it is deterministic."""

import csv
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from ml.models import nights as N

ROOT = Path(__file__).resolve().parents[2]
SCEN = ROOT / "demo" / "scenarios"
T0 = datetime(2020, 1, 1)


def load(name):
    with (SCEN / f"{name}.csv").open() as f:
        rows = list(csv.DictReader(f))
    readings = [{"timestamp": datetime.fromisoformat(r["timestamp"]), "glucose_mgdl": float(r["glucose_mgdl"])} for r in rows]
    return rows, readings, json.loads((SCEN / f"{name}.json").read_text())


@pytest.fixture(scope="module")
def titration():
    return load("titration_synthetic")


def night(n):
    return (T0 + timedelta(days=n, hours=22), T0 + timedelta(days=n + 1, hours=7))


def night_records(readings, alarms, nights):
    recs = []
    for n in nights:
        m = N.night_metrics(readings, alarms, night(n))
        codes, src = N.classify_night(readings, None, None, None, night(n))
        recs.append({**m, "night_date": night(n)[0].date(), "reason_codes": codes, "code_source": src})
    return recs


def alarms_of(comp):
    return [{**a, "started_at": datetime.fromisoformat(a["started_at"])} for a in comp["alarm_events"]]


def test_labeled_synthetic_and_loads_like_replay(titration):
    rows, readings, comp = titration
    assert comp["synthetic"] is True and comp["overlay_synthetic"] is True and comp["kind"] == "titration"
    assert readings[0]["timestamp"].year == 2020                       # obviously fake timestamps
    assert list(rows[0].keys()) == ["timestamp", "glucose_mgdl", "trend"]
    ts = [r["timestamp"] for r in readings]
    assert all(a < b for a, b in zip(ts, ts[1:]))
    assert max(b - a for a, b in zip(ts, ts[1:])) <= timedelta(minutes=10)   # dropouts are single readings


def test_companion_validates_against_contracts(titration):
    sys.path.insert(0, str(ROOT / "backend"))
    contracts = pytest.importorskip("app.contracts")
    _, _, comp = titration
    contracts.TitrationPlan.model_validate(comp["plan"])
    for a in comp["alarm_events"]:
        contracts.AlarmEvent.model_validate(a)
    for c in comp["symptom_checks"]:
        contracts.SymptomCheck.model_validate(c)
    for t in comp["injections"]:
        contracts.Treatment.model_validate(t)


def test_step2_days_3_to_7_is_the_worked_example(titration):
    _, readings, comp = titration
    alarms = alarms_of(comp)
    window_days = range(44, 49)                                        # step 2 (starts day 42) days 3-7
    window = night_records(readings, alarms, window_days)
    baseline = night_records(readings, alarms, range(0, 14))
    lo, hi = T0 + timedelta(days=44), T0 + timedelta(days=49)
    win_readings = [r for r in readings if lo <= r["timestamp"] < hi]
    checks = [{**c, "date": datetime.fromisoformat(c["date"]).date()} for c in comp["symptom_checks"]]
    v, _ = N.step_window_metrics(window, baseline, checks, [], window_readings=win_readings, window_days=5,
                                 window_dates=[(T0 + timedelta(days=d)).date() for d in window_days])
    assert len(win_readings) == 1390 and round(v["coverage_pct"], 1) == 96.5
    assert v["baseline_low_point"] == 98 and v["window_low_point"] == 76 and v["low_point_shift"] == -22
    assert sum(r["glucose_mgdl"] < 70 for r in win_readings) == 58 and round(v["tbr_pct"], 2) == 4.03
    assert v["near_misses"] == 2
    assert v["tolerance"] == {"fine": 2, "rough": 3, "cant_eat": 0, "missing": 0}
    assert v["ketone_risk_episodes"] == 0 and not v["insufficient"]
    assert all(r["level2_count"] == 0 for r in window)                 # no red: no level 2, no re-arm

    lows = [e for n in window_days for e in N.low_events(readings, None, alarms, night(n))]
    assert len(lows) == 1 and lows[0]["inferred_unfelt"]
    sv, _ = N.standing_window([], lows, [{"low_event_id": k, "answer": a} for k, a in comp["recall_answers"].items()])
    assert (sv["answered"], sv["unfelt_lows"], sv["unfelt_low_rate"]) == (1, 1, 1.0)
    assert comp["recall_answers"] == {lows[0]["low_event_id"]: "dont_remember"}


@pytest.mark.parametrize("shift_nights", [range(43, 48), range(45, 49)])
def test_low_point_median_is_robust_to_the_window_edge(titration, shift_nights):
    _, readings, comp = titration
    recs = night_records(readings, alarms_of(comp), shift_nights)
    import numpy as np
    assert np.median([r["low_point_mgdl"] for r in recs]) == 76


def test_step1_window_is_green(titration):
    _, readings, comp = titration
    alarms = alarms_of(comp)
    window = night_records(readings, alarms, range(16, 21))            # step 1 days 3-7
    baseline = night_records(readings, alarms, range(0, 14))
    lo, hi = T0 + timedelta(days=16), T0 + timedelta(days=21)
    v, _ = N.step_window_metrics(window, baseline, comp["symptom_checks"], [],
                                 window_readings=[r for r in readings if lo <= r["timestamp"] < hi], window_days=5)
    assert v["low_point_shift"] > -15 and v["tbr_pct"] <= 4.0 and v["near_misses"] < 2
    assert v["ketone_risk_episodes"] < 2 and v["coverage_pct"] >= 70


def test_regeneration_is_deterministic(tmp_path):
    from demo.make_scenarios import titration as build, write
    rows, comp = build()
    write(tmp_path, "titration_synthetic", rows, comp)
    for ext in ("csv", "json"):
        # line endings normalized: git's autocrlf may check the committed file out with CRLF
        fresh = (tmp_path / f"titration_synthetic.{ext}").read_bytes().replace(b"\r\n", b"\n")
        committed = (SCEN / f"titration_synthetic.{ext}").read_bytes().replace(b"\r\n", b"\n")
        assert fresh == committed
