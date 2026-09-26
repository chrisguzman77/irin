"""R4 check: a treated low (carbs at nadir + 20 min) is never inferred_unfelt;
a slow recovery with nothing logged is; slope 0.99 fires and 1.0 does not;
a 19-minute run never does; events are idempotent and served."""

from datetime import date, datetime, timedelta

import pytest

from app import store
from app.contracts import Reading, Settings, Treatment
from app.rounds.low_events import LowEventDetector
from app.rounds.nights_adapter import NightsAdapter
from tests.test_ledger import NIGHT, START, Sources, basal, carbs, flat_night


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(store.config, "IRIN_DB", str(tmp_path / "t.db"))
    store.init_db()


def make(src, is_demo=True):
    adapter = NightsAdapter(settings=Settings(basal_time="21:30"), readings_for=src.readings_for,
                            treatments_for=src.treatments_for, alarm_events_for=src.alarm_events_for,
                            presence_for=src.presence_for)
    return LowEventDetector(adapter=adapter, is_demo=lambda: is_demo)


def low_night(minutes_low: int, slope: float):
    """Flat until 02:00, a descent under 70 lasting `minutes_low` to a single
    nadir at 58, then a recovery of `slope` mg/dL per minute over 30 min, then
    flat at 110 (a rebound under 60 above the nadir, so never "treated" by the
    trace alone)."""
    rows = flat_night(n=48)  # 22:00 - 01:55
    t = START + timedelta(minutes=240)
    n_desc = max(1, minutes_low // 5)
    for i in range(n_desc):  # 68 down to 60; the nadir (58) is one reading, so the slope starts there
        v = 68.0 - (8.0 * i / max(1, n_desc - 1)) if n_desc > 1 else 68.0
        rows.append(Reading(timestamp=t + timedelta(minutes=5 * i), glucose_mgdl=round(v, 1), trend="SingleDown", source="replay"))
    nadir_t = t + timedelta(minutes=5 * n_desc)
    rows.append(Reading(timestamp=nadir_t, glucose_mgdl=58.0, trend="Flat", source="replay"))
    for j in range(1, 7):  # 30 minutes after the nadir at exactly `slope`
        rows.append(Reading(timestamp=nadir_t + timedelta(minutes=5 * j), glucose_mgdl=58.0 + slope * 5 * j,
                            trend="FortyFiveUp", source="replay"))
    t2 = nadir_t + timedelta(minutes=35)
    while t2 < START + timedelta(hours=9):
        rows.append(Reading(timestamp=t2, glucose_mgdl=max(110.0, 58.0 + slope * 30), trend="Flat", source="replay"))
        t2 += timedelta(minutes=5)
    return rows, nadir_t


def test_slow_recovery_with_nothing_logged_is_inferred_unfelt(db):
    rows, nadir_t = low_night(30, slope=0.5)
    [e] = make(Sources(readings=rows, treatments=[basal(datetime(2020, 1, 1, 21, 30))])).detect(NIGHT)
    assert e.inferred_unfelt is True and e.carbs_logged_within_30min is False and e.nadir_mgdl == 58.0
    assert e.minutes_below_70 >= 30 and e.night_date == NIGHT and e.is_demo is True and e.recovery_slope == pytest.approx(0.5)
    assert store.select_low_events(date(2020, 1, 1)) == [e]


def test_treated_low_is_never_inferred_unfelt(db):
    """nights.py counts carbs within 30 min of the EVENT START (the plan says
    nadir + 20 min; with a short descent both hold)."""
    rows, nadir_t = low_night(5, slope=0.5)
    src = Sources(readings=rows, treatments=[basal(datetime(2020, 1, 1, 21, 30)), carbs(nadir_t + timedelta(minutes=20))])
    [e] = make(src).detect(NIGHT)
    assert e.carbs_logged_within_30min is True and e.inferred_unfelt is False


def test_slope_boundary_0_99_fires_1_0_does_not(db):
    for slope, expected in [(0.99, True), (1.0, False)]:
        rows, _ = low_night(30, slope=slope)
        [e] = make(Sources(readings=rows, treatments=[basal(datetime(2020, 1, 1, 21, 30))])).detect(NIGHT)
        assert e.inferred_unfelt is expected, slope


def test_a_19_minute_run_never_fires(db):
    rows, _ = low_night(5, slope=2.0)  # one reading at 68, the nadir, then a fast climb: 15 min under 70
    [e] = make(Sources(readings=rows, treatments=[basal(datetime(2020, 1, 1, 21, 30))])).detect(NIGHT)
    assert e.minutes_below_70 < 20 and e.inferred_unfelt is False


def test_rebuild_replaces_and_a_clean_night_has_none(db):
    rows, _ = low_night(30, slope=0.5)
    det = make(Sources(readings=rows, treatments=[basal(datetime(2020, 1, 1, 21, 30))]))
    a = det.detect(NIGHT)
    b = det.detect(NIGHT)
    assert a == b and len(store.select_low_events(date(2020, 1, 1))) == 1
    assert make(Sources(readings=flat_night(), treatments=[])).detect(NIGHT) == []


def test_app_builds_low_events_with_the_night(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from app import auth, main

    monkeypatch.setattr(auth.config, "PIN", "1234")
    with TestClient(main.app) as c:
        first = main.runtime.datasource.rows[0][0]
        night = (first.date() - timedelta(days=1) if first.hour < 7 else first.date()).isoformat()
        assert c.post("/api/nights/build", json={"night_date": night}, headers={"X-PIN": "1234"}).status_code == 200
        lows = c.get("/api/low_events?days=3000").json()
        assert lows and all(e["is_demo"] is True and e["night_date"] == night for e in lows)  # The Save has its low


def test_carbs_after_the_window_end_still_make_a_late_low_treated(db):
    """A low in the last half hour of the night, carbs logged at 07:10: never unfelt."""
    rows = flat_night(n=105)  # 22:00 - 06:40
    t = START + timedelta(hours=8, minutes=45)  # 06:45
    for i, v in enumerate([68, 64, 60, 58, 60, 62, 64, 66, 68, 69, 69, 70, 72, 75]):
        rows.append(Reading(timestamp=t + timedelta(minutes=5 * i), glucose_mgdl=float(v), trend="Flat", source="replay"))
    late_carbs = carbs(datetime(2020, 1, 2, 7, 10))
    [e] = make(Sources(readings=rows, treatments=[basal(datetime(2020, 1, 1, 21, 30)), late_carbs])).detect(NIGHT)
    assert e.carbs_logged_within_30min is True and e.inferred_unfelt is False
    [e] = make(Sources(readings=rows, treatments=[basal(datetime(2020, 1, 1, 21, 30))])).detect(NIGHT)
    assert e.inferred_unfelt is True  # and without the carbs it is


def test_rebuild_after_a_recached_reading_leaves_no_stale_row(db):
    rows, _ = low_night(30, slope=0.5)
    src = Sources(readings=rows, treatments=[basal(datetime(2020, 1, 1, 21, 30))])
    det = make(src)
    [a] = det.detect(NIGHT)
    first_low = next(r for r in rows if r.glucose_mgdl < 70)
    rows[rows.index(first_low)] = first_low.model_copy(update={"glucose_mgdl": 72.0})  # a backfill correction
    [b] = det.detect(NIGHT)
    assert b.low_event_id != a.low_event_id and store.select_low_events(date(2020, 1, 1)) == [b]
