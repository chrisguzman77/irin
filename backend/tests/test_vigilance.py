"""R14(a) check: the predicted-low threshold rises by the plan's offset (10)
on days 1-7 after every step-up and is back on day 8; it stays at the base
when the option is off, before the plan, on step 0, and for a plan that is
not active; the actual-low threshold is identical throughout. Through the
real AlarmEngine: a forecast that would not warn at the base warns during the
step week, and an actual low under 70 alarms exactly as before."""

from datetime import date, datetime, timedelta

import pytest

from app.alarm import PREDICTED_LOW_THRESHOLD_MGDL as BASE, AlarmEngine
from app.clock import clock
from app.config import config  # noqa: F401  (puts the repo root, and hardware/, on sys.path)
from app.contracts import Forecast, Reading, Settings, TitrationPlan, TitrationStep, WatchOptions
from app.rounds.vigilance import effective_predicted_low_threshold, install
from hardware.mock import MockHAL

S0, S1, S2 = date(2020, 1, 1), date(2020, 1, 29), date(2020, 2, 26)


def plan(vigilance: bool = True, offset: float = 10.0, status: str = "active") -> TitrationPlan:
    return TitrationPlan(plan_id="p", drug_class="glp1", drug_label="x", started_at=S0, on_insulin=True, status=status,
                         steps=[TitrationStep(index=0, dose_label="a", planned_start=S0),
                                TitrationStep(index=1, dose_label="b", planned_start=S1),
                                TitrationStep(index=2, dose_label="c", planned_start=S2)],
                         options=WatchOptions(step_week_vigilance=vigilance, vigilance_offset_mgdl=offset))


@pytest.mark.parametrize("start", [S1, S2])
def test_plus_offset_on_days_1_to_7_after_each_step_up_and_back_on_day_8(start):
    s, p = Settings(), plan()
    for day in range(1, 8):
        assert effective_predicted_low_threshold(s, p, start + timedelta(days=day - 1)) == BASE + 10
    assert effective_predicted_low_threshold(s, p, start + timedelta(days=7)) == BASE  # day 8
    assert effective_predicted_low_threshold(s, p, start - timedelta(days=1)) == BASE  # the day before the step-up


def test_base_when_off_before_the_plan_on_step_0_or_no_plan():
    s = Settings()
    assert effective_predicted_low_threshold(s, plan(vigilance=False), S1) == BASE
    assert effective_predicted_low_threshold(s, plan(), S0 - timedelta(days=3)) == BASE
    for d in range(7):
        assert effective_predicted_low_threshold(s, plan(), S0 + timedelta(days=d)) == BASE  # step 0 is a start, not a step-up
    assert effective_predicted_low_threshold(s, None, S1) == BASE
    assert effective_predicted_low_threshold(s, plan(status="ended"), S1) == BASE
    # an offset that would lower or break the threshold never does
    assert effective_predicted_low_threshold(s, plan(offset=-20), S1) == BASE
    assert effective_predicted_low_threshold(s, plan(offset=float("nan")), S1) == BASE


def test_the_actual_low_threshold_is_identical_throughout():
    s = Settings()
    before = s.model_dump()
    for d in range(-3, 70):
        effective_predicted_low_threshold(s, plan(), S0 + timedelta(days=d))
    assert s.model_dump() == before and s.low_threshold == 70


@pytest.fixture
def rig():
    """The real engine with vigilance installed exactly as main.py does it."""
    hal = MockHAL()
    eng = AlarmEngine(Settings(), hal=hal)
    current: dict = {"plan": plan()}
    install(eng, eng.settings, lambda: current["plan"])
    yield eng, current
    clock.reset()


def at(day: date) -> None:
    clock.set(speed=60.0, start=datetime.combine(day, datetime.min.time()).replace(hour=12))


def feed(eng, mgdl: float, n: int = 2) -> None:
    for _ in range(n):
        eng.process_reading(Reading(timestamp=clock.now(), glucose_mgdl=120, trend="Flat", source="replay"))
        eng.process_forecast(Forecast(timestamp=clock.now(), predicted_mgdl=mgdl))


def test_a_forecast_that_would_not_warn_at_the_base_warns_in_the_step_week(rig):
    eng, current = rig
    near = BASE + 5  # above the base, under base + 10
    at(S1 + timedelta(days=2))
    assert eng.predicted_low_threshold() == BASE + 10
    feed(eng, near)
    assert eng.state.state == "pending" and eng.trigger == "predicted_low"
    # the same forecast outside the step week, or with the option off, does not warn
    for day, p in ((S1 + timedelta(days=7), plan()), (S1 + timedelta(days=2), plan(vigilance=False))):
        eng.reset()
        current["plan"] = p
        at(day)
        feed(eng, near)
        assert eng.state.state == "idle"


def test_an_actual_low_alarms_exactly_as_before(rig):
    eng, current = rig
    base_eng = AlarmEngine(Settings(), hal=MockHAL())  # no vigilance installed
    at(S1 + timedelta(days=1))
    for e in (eng, base_eng):
        e.process_reading(Reading(timestamp=clock.now(), glucose_mgdl=69, trend="SingleDown", source="replay"))
        assert e.state.state == "active" and e.trigger == "actual_low"
    eng.reset(), base_eng.reset()
    for e in (eng, base_eng):  # 70 is not an actual low in either, step week or not
        e.process_reading(Reading(timestamp=clock.now(), glucose_mgdl=70, trend="Flat", source="replay"))
        assert e.state.state == "idle"
    assert eng.settings.low_threshold == base_eng.settings.low_threshold == 70


def test_a_broken_plan_lookup_falls_back_to_the_base(rig):
    eng, _ = rig

    def boom():
        raise RuntimeError("store down")

    install(eng, eng.settings, boom)
    at(S1)
    assert eng.predicted_low_threshold() == BASE
