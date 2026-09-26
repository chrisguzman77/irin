"""R3 check: a NightRecord appears at the window end under 60x replay; each
reason-code rule at its boundary through the adapter (a snack 2 h 59 min
before night start is late_meal, 3 h 01 min is not; a basal 61 min late is
basal_late; 92 readings adequate, 91 stale); a rebuilt night is identical;
Brain-only nights infer and never assume clean; every number is George's."""

import inspect
from datetime import date, datetime, timedelta

import pytest

from app import store
from app.clock import clock
from app.contracts import AlarmEvent, PresenceState, Reading, Settings, Treatment
from app.rounds import ledger as ledger_mod, nights_adapter as adapter_mod
from app.rounds.ledger import Ledger
from app.rounds.nights_adapter import NightsAdapter
from app.scheduler import Scheduler

NIGHT = date(2020, 1, 1)  # Rounds keys a night by the EVENING it starts on (nights.py, the fixtures)
START = datetime(2020, 1, 1, 22, 0)
END = datetime(2020, 1, 2, 7, 0)


def flat_night(mgdl=110.0, n=108, start=START, step=5):
    """A flat night: n readings on the 5-minute grid from start."""
    return [Reading(timestamp=start + timedelta(minutes=step * i), glucose_mgdl=mgdl, trend="Flat", source="replay")
            for i in range(n)]


class Sources:
    def __init__(self, readings=None, treatments=None, alarm_events=None, presence=None):
        self.readings = readings or []
        self.treatments = treatments or []
        self.alarm_events = alarm_events or []
        self.presence = presence or []

    def readings_for(self, a, b):
        return [r for r in self.readings if a <= r.timestamp <= b]

    def treatments_for(self, a, b):
        return [t for t in self.treatments if a <= t.timestamp <= b]

    def alarm_events_for(self, a, b):
        return [e for e in self.alarm_events if a <= e.started_at <= b]

    def presence_for(self, a, b):
        return list(self.presence)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(store.config, "IRIN_DB", str(tmp_path / "t.db"))
    store.init_db()


def make(src, settings=None, brain_only=False, is_demo=True):
    settings = settings or Settings(basal_time="21:30")
    adapter = NightsAdapter(settings=settings, readings_for=src.readings_for, treatments_for=src.treatments_for,
                            alarm_events_for=src.alarm_events_for, presence_for=src.presence_for,
                            brain_only=lambda: brain_only)
    return Ledger(adapter=adapter, is_demo=lambda: is_demo)


def carbs(at):
    return Treatment(timestamp=at, kind="carbs", carbs_g=20.0)


def basal(at):
    return Treatment(timestamp=at, kind="basal", insulin_units=22.0, confirmed=True)


def test_clean_night_record_and_rebuild_is_identical(db):
    src = Sources(readings=flat_night(), treatments=[basal(datetime(2020, 1, 1, 21, 30))])
    led = make(src)
    r = led.build_night(NIGHT)
    assert r.night_date == NIGHT and (r.window_start, r.window_end) == (START, END)
    assert r.reason_codes == ["clean"] and r.code_source == "logged" and r.is_demo
    assert r.coverage_pct == 100.0 and r.minutes_below_70 == 0 and r.low_point_mgdl == 110.0
    assert store.select_night_record(NIGHT) == r
    assert led.build_night(NIGHT) == r and len(store.select_night_records(date(2020, 1, 1))) == 1


def test_late_meal_boundary(db):
    for minutes_before, expected in [(2 * 60 + 59, ["late_meal"]), (3 * 60 + 1, ["clean"])]:
        src = Sources(readings=flat_night(), treatments=[carbs(START - timedelta(minutes=minutes_before)),
                                                         basal(datetime(2020, 1, 1, 21, 30))])
        assert make(src).build_night(NIGHT).reason_codes == expected, minutes_before


def test_basal_late_boundary(db):
    for late_min, expected in [(61, ["basal_late"]), (59, ["clean"])]:
        src = Sources(readings=flat_night(), treatments=[basal(datetime(2020, 1, 1, 21, 30) + timedelta(minutes=late_min))])
        assert make(src).build_night(NIGHT).reason_codes == expected, late_min
    src = Sources(readings=flat_night(), treatments=[carbs(datetime(2020, 1, 1, 12, 0))])  # a logging patient, no basal
    assert make(src).build_night(NIGHT).reason_codes == ["basal_missed"]


def test_coverage_boundary_92_adequate_91_stale(db):
    src = Sources(readings=flat_night(n=92), treatments=[basal(datetime(2020, 1, 1, 21, 30))])
    r = make(src).build_night(NIGHT)
    assert "stale" not in r.reason_codes and r.coverage_pct == pytest.approx(85.2, abs=0.1)
    src = Sources(readings=flat_night(n=91), treatments=[basal(datetime(2020, 1, 1, 21, 30))])
    r = make(src).build_night(NIGHT)
    assert "stale" in r.reason_codes and r.coverage_pct == pytest.approx(84.3, abs=0.1)


def test_away_toggle_and_exercise_codes(db):
    away = PresenceState(mode="away", source="toggle", since=datetime(2020, 1, 1, 23, 0))
    src = Sources(readings=flat_night(), treatments=[basal(datetime(2020, 1, 1, 21, 30)),
                                                     Treatment(timestamp=datetime(2020, 1, 1, 18, 0), kind="note", text="gym after work")],
                  presence=[away])
    codes = make(src).build_night(NIGHT).reason_codes
    assert "away" in codes and "exercise" in codes


def test_brain_only_infers_and_never_assumes_clean(db):
    src = Sources(readings=flat_night(), treatments=[carbs(START - timedelta(hours=1))],  # a logged snack it cannot see
                  presence=[PresenceState(mode="away", source="toggle", since=datetime(2020, 1, 1, 23, 0))])
    r = make(src, brain_only=True).build_night(NIGHT)
    assert r.code_source == "inferred" and "away" not in r.reason_codes and "late_meal" not in r.reason_codes
    assert r.reason_codes == ["clean"]  # a flat trace with nothing visible: clean, labeled inferred (George's decision)
    # a glucose rise faster than 2 mg/dL/min in the first 2 h is an inferred late meal
    rising = flat_night(n=6) + [Reading(timestamp=START + timedelta(minutes=5 * i), glucose_mgdl=110 + 12 * (i - 6),
                                         trend="SingleUp", source="replay") for i in range(6, 22)]
    rising += [Reading(timestamp=START + timedelta(minutes=5 * i), glucose_mgdl=302, trend="Flat", source="replay")
               for i in range(22, 108)]
    r = make(Sources(readings=rising), brain_only=True).build_night(NIGHT)
    assert "late_meal" in r.reason_codes and r.code_source == "inferred"


def test_treated_low_and_alarm_ids_and_the_low_night_metrics(db):
    readings = flat_night(n=48)  # 22:00 - 01:55 flat
    readings += [Reading(timestamp=START + timedelta(minutes=5 * i), glucose_mgdl=v, trend="Flat", source="replay")
                 for i, v in zip(range(48, 60), [80, 68, 62, 58, 55, 60, 75, 95, 120, 130, 130, 130])]
    readings += flat_night(mgdl=130, n=48, start=START + timedelta(minutes=300))
    ev = AlarmEvent(event_id="ae-1", tier="actual_low", started_at=START + timedelta(minutes=49 * 5), crossed_actual=True)
    src = Sources(readings=readings, treatments=[basal(datetime(2020, 1, 1, 21, 30)), carbs(START + timedelta(minutes=52 * 5))],
                  alarm_events=[ev])
    r = make(src).build_night(NIGHT)
    assert "treated_low" in r.reason_codes and r.alarm_event_ids == ["ae-1"]
    assert r.minutes_below_70 == 25 and r.low_point_mgdl is not None and r.low_point_mgdl < 70 and r.tbr_pct > 0


def test_record_appears_at_window_end_under_replay(db):
    """The scheduler's ledger job at night_window_end, 60x: the record exists."""
    src = Sources(readings=flat_night(), treatments=[basal(datetime(2020, 1, 1, 21, 30))])
    led = make(src)
    built = []
    clock.set(speed=60.0, start=datetime(2020, 1, 2, 6, 59))
    try:
        s = Scheduler(Settings())
        s.register("ledger", "07:00", lambda d: built.append(led.build_night(led.night_ended_on(d))))
        assert s.tick() == []
        clock.advance(2 * 60)  # 07:01
        assert s.tick() == ["ledger"] and built[0].night_date == NIGHT
        assert store.select_night_record(NIGHT) is not None
    finally:
        clock.reset()


def test_nothing_is_computed_here():
    """Every metric and reason code comes from ml/models/nights.py."""
    for mod in (ledger_mod, adapter_mod):
        src = inspect.getsource(mod)
        assert "numpy" not in src and "median" not in src and "< 70" not in src and "LOW" not in src


def test_app_serves_nights_and_builds_on_demand(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from app import auth, main

    monkeypatch.setattr(auth.config, "PIN", "1234")
    with TestClient(main.app) as c:
        assert c.get("/api/nights").json() == []
        first = main.runtime.datasource.rows[0][0]  # the scenario's night starts the evening before its first row
        night = (first.date() - timedelta(days=1) if first.hour < 7 else first.date()).isoformat()
        assert c.post("/api/nights/build", json={"night_date": night}).status_code == 401
        r = c.post("/api/nights/build", json={"night_date": night}, headers={"X-PIN": "1234"})
        assert r.status_code == 200 and r.json()["is_demo"] is True and r.json()["night_date"] == night
        assert r.json()["code_source"] == "logged" and r.json()["coverage_pct"] > 0
        assert [n["night_date"] for n in c.get("/api/nights?days=3000").json()] == [night]
