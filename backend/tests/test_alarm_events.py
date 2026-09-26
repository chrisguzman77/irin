"""R2 check: The Save-shaped run through the engine yields ONE episode with
escalated = True and crossed_actual = True; a warning-only run yields
crossed_actual = False; presence flicker both ways (a brief dropout while
present still reads home; a single blip outside the start window in an empty
room still reads away); an undriven mock reads unknown; the recorder never
calls into alarm.py; the named tier test still passes (test_alarm.py)."""

import inspect
from datetime import datetime, timedelta

import pytest

from app import store
from app.alarm import AlarmEngine
from app.clock import clock
from app.contracts import Forecast, Reading, Settings
from app.rounds import alarm_events as mod
from app.rounds.alarm_events import AlarmEventRecorder
from hardware.mock import MockHAL

T0 = datetime(2020, 1, 1, 2, 0)


def reading(mgdl, stale=False):
    return Reading(timestamp=clock.now(), glucose_mgdl=mgdl, trend="Flat", source="replay", is_stale=stale)


def forecast(mgdl):
    return Forecast(timestamp=clock.now(), predicted_mgdl=mgdl)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setattr(store.config, "IRIN_DB", str(tmp_path / "t.db"))
    store.init_db()
    clock.set(speed=60.0, start=T0)
    hal = MockHAL()
    eng = AlarmEngine(Settings(), hal=hal)
    rec = AlarmEventRecorder(is_demo=lambda: True)
    eng.on_transition(rec)
    yield eng, hal, rec
    clock.reset()


def advance(eng, rec, minutes, present=None):
    """Clock ticks of 30 s: the engine's deadlines and one radar sample each."""
    for _ in range(int(minutes * 2)):
        clock.advance(30)
        eng.tick()
        rec.sample(present() if callable(present) else present)


def recover(eng):
    eng.process_reading(reading(85))
    eng.process_reading(reading(119))  # two readings back above: idle


def test_the_save_shaped_episode_is_one_event_escalated_and_crossed(rig):
    eng, hal, rec = rig
    eng.process_reading(reading(102))
    eng.process_forecast(forecast(78))
    eng.process_forecast(forecast(74))  # pending
    advance(eng, rec, 4, present=True)
    eng.process_reading(reading(64))  # the reading crosses before anyone answers: actual_low, same episode
    advance(eng, rec, 5.5, present=True)  # the strobe step
    assert eng.acknowledge("app")
    advance(eng, rec, 15, present=True)  # still low at 15 min: re-armed
    eng.process_reading(reading(62))
    advance(eng, rec, 1, present=True)
    eng.acknowledge("device")
    recover(eng)
    assert eng.state.state == "idle" and len(rec.events) == 1
    [e] = rec.events
    assert e.tier == "actual_low" and e.escalated is True and e.crossed_actual is True
    assert e.ack_source == "app" and e.acknowledged_at is not None and e.rearm_count == 1
    assert e.presence_during == "home" and e.is_demo is True and abs((e.started_at - T0).total_seconds()) < 1
    assert store.select_alarm_events(T0 - timedelta(hours=1))[0] == e
    assert e.event_id.startswith("ae-20200101T020000") and e.event_id.endswith("-actual_low")


def test_warning_only_run_is_a_near_miss(rig):
    eng, hal, rec = rig
    eng.process_reading(reading(95))
    eng.process_forecast(forecast(80))
    eng.process_forecast(forecast(79))  # pending
    advance(eng, rec, 2, present=False)
    eng.acknowledge("device")  # the warning ends here
    [e] = rec.events
    assert e.tier == "predicted_low" and e.crossed_actual is False and e.escalated is False
    assert e.ack_source == "device" and abs((e.acknowledged_at - clock.now()).total_seconds()) < 1
    eng.process_forecast(forecast(90))
    eng.process_forecast(forecast(92))
    assert len(rec.events) == 1  # the acked episode's recovery writes nothing more


def test_timed_out_warning_counts_as_escalated(rig):
    eng, hal, rec = rig
    eng.process_reading(reading(95))
    eng.process_forecast(forecast(80))
    eng.process_forecast(forecast(79))
    advance(eng, rec, 5.5, present=True)  # 5 min unacknowledged: active predicted_low, no crossing
    assert eng.state.state == "active"
    eng.acknowledge("app")
    advance(eng, rec, 16, present=True)  # not below: no re-arm
    recover(eng)
    [e] = rec.events
    assert e.tier == "predicted_low" and e.escalated is True and e.crossed_actual is False


def test_presence_flicker_both_ways(rig):
    eng, hal, rec = rig
    # present with a brief dropout: home
    eng.process_reading(reading(60))
    samples = iter([True, True, None, False, False, True, True, True, True, True])
    advance(eng, rec, 5, present=lambda: next(samples, True))
    recover(eng)
    assert rec.events[-1].presence_during == "home"
    # empty room with one stray blip well after the start: away
    advance(eng, rec, 3, present=False)
    eng.process_reading(reading(58))
    blips = iter([False] * 10 + [True] + [False] * 9)
    advance(eng, rec, 10, present=lambda: next(blips, False))
    recover(eng)
    assert rec.events[-1].presence_during == "away"
    # a detection just before the start (the pre-start buffer) makes it home
    advance(eng, rec, 1, present=True)
    advance(eng, rec, 0.5, present=False)
    eng.process_reading(reading(58))
    advance(eng, rec, 10, present=False)
    recover(eng)
    assert rec.events[-1].presence_during == "home"


def test_undriven_mock_and_brain_only_read_unknown(rig):
    eng, hal, rec = rig
    eng.process_reading(reading(58))
    advance(eng, rec, 6, present=None)
    recover(eng)
    assert rec.events[-1].presence_during == "unknown"
    rec.brain_only = lambda: True
    eng.process_reading(reading(58))
    advance(eng, rec, 6, present=True)
    recover(eng)
    assert rec.events[-1].presence_during == "unknown"


def test_high_and_stale_episodes_are_recorded_by_tier(rig):
    eng, hal, rec = rig
    eng.process_reading(reading(260))
    eng.process_reading(reading(240))  # back under the high threshold: idle
    eng.process_reading(reading(120, stale=True))
    eng.process_reading(reading(120))
    assert [e.tier for e in rec.events] == ["high", "stale"]
    assert all(e.crossed_actual is False and e.acknowledged_at is None for e in rec.events)


def test_reset_drops_the_open_episode_and_writes_nothing(rig):
    eng, hal, rec = rig
    eng.process_reading(reading(58))
    advance(eng, rec, 2, present=True)
    rec.reset()
    eng.reset()  # the mode switch
    assert rec.events == [] and rec.open is None and store.select_alarm_events(T0 - timedelta(days=1)) == []


def test_recorder_observes_only():
    src = inspect.getsource(mod)
    assert "AlarmEngine" not in src and ".acknowledge(" not in src and "process_" not in src
    from app import alarm

    assert "get_presence" not in inspect.getsource(alarm)


def test_app_serves_alarm_events():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        assert c.get("/api/alarm_events").json() == []


def test_a_low_replacing_an_indicator_starts_a_fresh_episode(rig):
    """A high that has been on for hours, then a warning with the sleeper present:
    the low's started_at, start window, and id are the warning's, not the high's."""
    eng, hal, rec = rig
    eng.process_reading(reading(260))  # high, room empty
    advance(eng, rec, 180, present=False)
    warn_at = clock.now()
    eng.process_forecast(forecast(78))
    eng.process_forecast(forecast(74))  # pending predicted_low replaces the high
    advance(eng, rec, 3, present=True)
    eng.acknowledge("app")
    assert [e.tier for e in rec.events] == ["high", "predicted_low"]
    low = rec.events[-1]
    assert abs((low.started_at - warn_at).total_seconds()) < 1 and low.presence_during == "home"
    assert rec.events[0].presence_during == "away" and rec.events[0].started_at < low.started_at


def test_two_episodes_in_one_second_keep_two_rows(rig):
    eng, hal, rec = rig
    eng.process_reading(reading(260))
    eng.process_reading(reading(240))
    eng.process_reading(reading(261))
    eng.process_reading(reading(240))
    assert len(rec.events) == 2 and len(store.select_alarm_events(T0 - timedelta(days=1))) == 2


def test_an_observer_error_never_reaches_the_engine(rig):
    eng, hal, rec = rig
    rec.brain_only = lambda: 1 / 0
    eng.process_reading(reading(58))
    eng.process_reading(reading(85))
    eng.process_reading(reading(119))  # closes the episode: the verdict raises, the engine still goes idle quietly
    assert eng.state.state == "idle" and hal.calls[-1][0] in ("set_leds", "stop_sound")
