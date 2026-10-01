"""Step 4: the named tier test (in replay, acknowledge a predicted-low warning,
let the scenario cross the actual threshold anyway, assert the full alarm
fires) plus a walk of every transition. Timers are driven with clock.advance()
and engine.tick(); nothing sleeps. Outputs are asserted on the mock HAL."""

import asyncio
from datetime import datetime, timedelta

import pytest

from app.alarm import ESCALATION_MIN, PENDING_TIMEOUT_MIN, REARM_MIN, AlarmEngine, Transition
from app.clock import clock
from app.config import config
from app.contracts import Forecast, Reading, Settings
from app.datasource.replay import ReplayDataSource
from hardware.mock import MockHAL

T0 = datetime(2020, 1, 1, 12, 0)  # daytime: outside the default night window (quiet hours)
NIGHT = datetime(2020, 1, 2, 3, 0)


def reading(mgdl: float, stale: bool = False) -> Reading:
    return Reading(timestamp=clock.now(), glucose_mgdl=mgdl, trend="Flat", source="replay", is_stale=stale)


def forecast(mgdl: float) -> Forecast:
    return Forecast(timestamp=clock.now(), predicted_mgdl=mgdl)


@pytest.fixture
def engine():
    clock.set(speed=60.0, start=T0)
    hal = MockHAL()
    eng = AlarmEngine(Settings(), hal=hal)
    log: list[Transition] = []
    eng.on_transition(log.append)
    hal.calls.clear()
    yield eng, hal, log
    clock.reset()


def warn(eng, n=2):
    for _ in range(n):
        eng.process_forecast(forecast(60))


def sounds(hal):
    return [c[1] for c in hal.calls if c[0] == "play_sound"]


def leds(hal):
    return [c[1] for c in hal.calls if c[0] == "set_leds"]


# --- the named tier test ---


def test_named_tier_test_warning_ack_never_suppresses_the_actual_low():
    """The Save at 60x with a perfect 30-minute forecaster (the CSV's own value
    six rows ahead; step 6 plugs in the real one)."""
    ds = ReplayDataSource(config.scenario_path, speed=60.0)
    hal = MockHAL()
    log: list[Transition] = []

    async def run():
        await ds.start()
        eng = AlarmEngine(Settings(), hal=hal)
        eng.on_transition(log.append)
        rows = ds.rows
        acked_warning = False
        full_fired_at = None
        for i in range(len(rows)):
            r = await ds.get_latest()
            eng.process_reading(r)
            if i + 6 < len(rows):
                eng.process_forecast(Forecast(timestamp=r.timestamp, predicted_mgdl=rows[i + 6][1]))
            if eng.state.state == "pending" and not acked_warning:
                assert eng.trigger == "predicted_low" and r.glucose_mgdl >= 70  # warned BEFORE the crossing
                assert eng.acknowledge("app") is True
                acked_warning = True
                assert eng.state.state == "idle"
            if eng.state.state == "active" and eng.trigger == "actual_low" and full_fired_at is None:
                full_fired_at = r.timestamp
                assert eng.state.acknowledged_at is None  # fresh, never inherits the warning's ack
                assert r.glucose_mgdl < 70
            eng.tick()
            clock.advance(5 * 60)
        await ds.stop()
        assert acked_warning and full_fired_at is not None
        kinds = [(t.old_state, t.new_state, t.trigger_type) for t in log]
        assert ("idle", "pending", "predicted_low") in kinds
        assert ("pending", "idle", None) in kinds  # the warning ack
        assert ("idle", "active", "actual_low") in kinds  # the full alarm anyway
        assert kinds.count(("idle", "pending", "predicted_low")) == 1  # never re-warned in the same episode
        assert "alarm_urgent" in sounds(hal) and "full" in leds(hal)
        assert eng.state.state == "idle"  # the scenario recovers

    asyncio.run(run())


# --- the walk ---


def test_idle_to_pending_needs_n_consecutive_predicted_lows(engine):
    eng, hal, log = engine
    eng.process_forecast(forecast(60))
    eng.process_forecast(forecast(90))  # streak broken (85 is the predicted-low threshold)
    eng.process_forecast(forecast(60))
    assert eng.state.state == "idle"
    eng.process_forecast(forecast(60))
    assert eng.state.state == "pending" and eng.trigger == "predicted_low"
    assert sounds(hal) == ["alarm_soft"] and leds(hal)[-1] == "warning"


def test_predicted_threshold_is_85_on_the_forecast_and_70_on_the_reading(engine):
    eng, hal, log = engine
    eng.process_forecast(forecast(84))
    eng.process_forecast(forecast(84))
    assert eng.state.state == "pending"
    eng.process_reading(reading(84))  # 84 actual is not a low
    assert eng.state.state == "pending"


def test_missing_forecast_resets_the_count_but_never_clears_a_warning(engine):
    eng, hal, log = engine
    eng.process_forecast(forecast(60))
    eng.process_no_forecast()  # a stale or gapped hour between two lows
    eng.process_forecast(forecast(60))
    assert eng.state.state == "idle"  # the count restarted
    eng.process_forecast(forecast(60))
    assert eng.state.state == "pending"
    for _ in range(5):
        eng.process_no_forecast()
    assert eng.state.state == "pending"  # stays on through missing forecasts
    eng.process_forecast(forecast(95))
    eng.process_no_forecast()
    eng.process_forecast(forecast(95))
    assert eng.state.state == "pending"  # the two recoveries must be consecutive real forecasts
    eng.process_forecast(forecast(95))
    assert eng.state.state == "idle"


def test_predictive_disabled_never_warns(engine):
    eng, hal, log = engine
    eng.settings.predictive_enabled = False
    warn(eng, 5)
    assert eng.state.state == "idle" and sounds(hal) == []


def test_pending_to_active_on_crossing(engine):
    eng, hal, log = engine
    warn(eng)
    eng.process_reading(reading(68))
    assert eng.state.state == "active" and eng.trigger == "actual_low"
    assert eng.state.acknowledged_at is None and sounds(hal)[-1] == "alarm_urgent" and leds(hal)[-1] == "full"
    assert [c for c in hal.calls if c[0] == "play_sound"][-1][2] == 1.0  # full volume, always


def test_pending_to_active_on_5_min_timeout(engine):
    eng, hal, log = engine
    warn(eng)
    clock.advance(PENDING_TIMEOUT_MIN * 60 - 1)
    eng.tick()
    assert eng.state.state == "pending"
    clock.advance(1)
    eng.tick()
    assert eng.state.state == "active" and log[-1].escalated and sounds(hal)[-1] == "alarm_urgent"


def test_timed_out_warning_then_ack_then_crossing_fires_a_fresh_full_alarm(engine):
    """Review finding: the ack of a timed-out warning must never swallow the actual crossing."""
    eng, hal, log = engine
    warn(eng)
    clock.advance(PENDING_TIMEOUT_MIN * 60)
    eng.tick()
    assert eng.state.state == "active" and eng.trigger == "predicted_low"
    assert eng.acknowledge("app") is True and eng.state.state == "acknowledged"
    n = len(hal.calls)
    eng.process_reading(reading(62))  # the actual crossing, 14 min before any re-arm
    assert eng.state.state == "active" and eng.trigger == "actual_low"
    assert eng.state.acknowledged_at is None and log[-1].old_state == "acknowledged"
    assert sounds(hal)[-1] == "alarm_urgent" and leds(hal)[-1] == "full" and len(hal.calls) > n


def test_timed_out_warning_unacked_crossing_emits_actual_low_transition(engine):
    eng, hal, log = engine
    warn(eng)
    clock.advance(PENDING_TIMEOUT_MIN * 60)
    eng.tick()
    eng.process_reading(reading(62))
    assert (log[-1].old_state, log[-1].new_state, log[-1].trigger_type) == ("active", "active", "actual_low")
    assert eng.trigger == "actual_low"  # R2's crossed_actual can see it


def test_timed_out_warning_still_strobes_after_5_more_minutes(engine):
    eng, hal, log = engine
    warn(eng)
    clock.advance(PENDING_TIMEOUT_MIN * 60)
    eng.tick()
    assert leds(hal)[-1] == "full"
    clock.advance(ESCALATION_MIN * 60)
    eng.tick()
    assert leds(hal)[-1] == "strobe" and log[-1].escalated and log[-1].old_state == "active"


def test_stale_respects_quiet_hours(engine):
    eng, hal, log = engine
    clock.set(speed=60.0, start=NIGHT)
    eng.process_reading(reading(120, stale=True))
    assert eng.trigger == "stale" and sounds(hal) == []  # indicator only at night


def test_pending_to_idle_on_two_recovered_forecasts_and_rewarns_next_episode(engine):
    eng, hal, log = engine
    warn(eng)
    eng.process_forecast(forecast(90))
    assert eng.state.state == "pending"
    eng.process_forecast(forecast(90))
    assert eng.state.state == "idle" and hal.calls[-2:] == [("stop_sound",), ("set_leds", "ambient")]
    warn(eng)  # a fresh crossing is a NEW episode and warns again
    assert eng.state.state == "pending"


def test_warning_ack_is_final_for_the_episode_and_never_preacks_the_low(engine):
    eng, hal, log = engine
    warn(eng)
    assert eng.acknowledge("device") is True and eng.state.state == "idle" and log[-1].ack_source == "device"
    warn(eng, 5)
    assert eng.state.state == "idle"  # no re-warn in the acked episode
    eng.process_reading(reading(65))
    assert eng.state.state == "active" and eng.trigger == "actual_low" and eng.state.acknowledged_at is None
    assert sounds(hal)[-1] == "alarm_urgent"


def test_idle_straight_to_active_without_a_warning(engine):
    eng, hal, log = engine
    eng.process_reading(reading(66))
    assert eng.state.state == "active" and eng.trigger == "actual_low"


def test_active_ack_rearm_and_recovery(engine):
    eng, hal, log = engine
    eng.process_reading(reading(60))
    assert eng.acknowledge("app") is True
    assert eng.state.state == "acknowledged"
    assert abs(eng.state.acknowledged_at - clock.now()) < timedelta(seconds=5)  # 60x: now() moves between calls
    assert hal.calls[-2:] == [("stop_sound",), ("set_leds", "ambient")]

    clock.advance(REARM_MIN * 60 - 1)
    eng.tick()
    assert eng.state.state == "acknowledged"
    clock.advance(1)
    eng.tick()
    assert eng.state.state == "rearmed" and sounds(hal)[-1] == "alarm_urgent"  # still 60: re-armed

    assert eng.acknowledge("device") is True and eng.state.state == "acknowledged"
    clock.advance(REARM_MIN * 60)
    eng.tick()
    assert eng.state.state == "rearmed"  # repeats every 15 min

    eng.process_reading(reading(72))
    assert eng.state.state == "rearmed"  # one recovered reading is not enough
    eng.process_reading(reading(74))
    assert eng.state.state == "idle" and hal.calls[-1] == ("set_leds", "ambient")


def test_acknowledged_not_rearmed_when_recovered_at_15_min(engine):
    eng, hal, log = engine
    eng.process_reading(reading(60))
    eng.acknowledge("app")
    eng.process_reading(reading(75))  # one reading above: not closed yet
    clock.advance(REARM_MIN * 60)
    eng.tick()
    assert eng.state.state == "acknowledged"  # above threshold: no re-arm
    eng.process_reading(reading(76))
    assert eng.state.state == "idle"


def test_escalation_after_5_min_unacknowledged(engine):
    eng, hal, log = engine
    eng.process_reading(reading(60))
    clock.advance(ESCALATION_MIN * 60)
    eng.tick()
    assert eng.state.state == "active" and log[-1].escalated and log[-1].old_state == "active"
    assert leds(hal)[-1] == "strobe"
    n = len(log)
    clock.advance(60)
    eng.tick()
    assert len(log) == n  # escalates once per (re)arm


def test_stale_during_a_low_keeps_it_sounding(engine):
    eng, hal, log = engine
    eng.process_reading(reading(60))
    n_calls = len(hal.calls)
    eng.process_reading(reading(60, stale=True))
    assert eng.state.state == "active" and eng.trigger == "actual_low"
    assert len(hal.calls) == n_calls  # nothing stopped, nothing chirped over it
    eng.process_reading(reading(80, stale=True))  # a stale "recovery" never closes the episode
    assert eng.state.state == "active"


def test_stale_alone_is_a_one_shot_indicator_cleared_by_a_fresh_reading(engine):
    eng, hal, log = engine
    eng.process_reading(reading(120, stale=True))
    assert eng.state.state == "active" and eng.trigger == "stale" and sounds(hal) == ["chirp"]
    eng.process_reading(reading(120, stale=True))
    assert sounds(hal) == ["chirp"]  # one shot
    assert eng.acknowledge("app") is False  # nothing to acknowledge
    eng.process_reading(reading(120))
    assert eng.state.state == "idle"


def test_high_is_one_shot_no_ack_no_rearm_and_clears_on_recrossing(engine):
    eng, hal, log = engine
    eng.process_reading(reading(260))
    assert eng.state.state == "active" and eng.trigger == "high" and sounds(hal) == ["chirp"]
    eng.process_reading(reading(270))
    assert sounds(hal) == ["chirp"] and eng.acknowledge("app") is False
    clock.advance(60 * 60)
    eng.tick()
    assert sounds(hal) == ["chirp"]  # no reminder unless the off-by-default setting is on
    eng.process_reading(reading(240))
    assert eng.state.state == "idle"


def test_high_respects_quiet_hours_but_lows_do_not(engine):
    eng, hal, log = engine
    clock.set(speed=60.0, start=NIGHT)
    eng.process_reading(reading(260))
    assert eng.state.state == "active" and eng.trigger == "high"
    assert sounds(hal) == []  # indicator only at night
    eng.process_reading(reading(60))
    assert sounds(hal) == ["alarm_urgent"]  # the low overrides quiet hours (invariant 3)


def test_high_reminder_when_enabled(engine):
    eng, hal, log = engine
    eng.settings.high_alert_mode = "remind"
    eng.settings.high_remind_hours = 2.0
    eng.process_reading(reading(260))
    clock.advance(2 * 3600 - 60)
    eng.tick()
    assert sounds(hal) == ["chirp"]
    clock.advance(60)
    eng.tick()
    assert sounds(hal) == ["chirp", "chirp"]


def test_priority_low_replaces_high_and_stale_without_inheriting(engine):
    eng, hal, log = engine
    eng.process_reading(reading(260))
    assert eng.trigger == "high"
    warn(eng)
    assert eng.state.state == "pending" and eng.trigger == "predicted_low"  # predicted low beats high
    eng.process_reading(reading(120, stale=True))
    assert eng.state.state == "pending"  # stale never touches a running low tier
    eng.process_reading(reading(60))
    assert eng.state.state == "active" and eng.trigger == "actual_low" and eng.state.acknowledged_at is None


def test_observers_see_every_transition_and_can_unregister(engine):
    eng, hal, log = engine
    seen = []
    off = eng.on_transition(seen.append)
    eng.process_reading(reading(60))
    eng.acknowledge("app")
    assert [(t.old_state, t.new_state) for t in seen] == [("idle", "active"), ("active", "acknowledged")]
    assert seen[0].reading.glucose_mgdl == 60 and seen[1].ack_source == "app"
    off()
    eng.process_reading(reading(80))
    eng.process_reading(reading(80))
    assert len(seen) == 2 and eng.state.state == "idle"


def test_reset_returns_to_idle_and_silences(engine):
    eng, hal, log = engine
    eng.process_reading(reading(60))
    eng.reset()
    assert eng.state.state == "idle" and hal.calls[-2:] == [("stop_sound",), ("set_leds", "ambient")]


def test_predicted_low_threshold_is_the_one_vigilance_hook(engine):
    eng, hal, log = engine
    eng.predicted_low_threshold = lambda: 80.0  # R14a raises this and nothing else
    eng.process_forecast(forecast(75))
    eng.process_forecast(forecast(75))
    assert eng.state.state == "pending"
    eng.process_reading(reading(75))  # the actual-low path is untouched: 75 is not a low
    assert eng.state.state == "pending"


# --- audit fixes ---


def test_acked_low_rearms_on_clock_time_even_if_the_sensor_drops_out(engine):
    eng, hal, log = engine
    eng.process_reading(reading(60))
    eng.acknowledge("app")
    eng.process_reading(reading(60, stale=True))  # the sensor dropped out after the ack
    clock.advance(REARM_MIN * 60)
    eng.tick()  # no new fresh reading ever arrived
    assert eng.state.state == "rearmed" and sounds(hal)[-1] == "alarm_urgent"
    assert eng.last_reading.is_stale  # the stale state stays visible


def test_acked_low_with_no_reading_at_all_after_ack_rearms(engine):
    eng, hal, log = engine
    eng.process_reading(reading(60))
    eng.acknowledge("device")
    clock.advance(REARM_MIN * 60)
    eng.tick()
    assert eng.state.state == "rearmed"


def test_stale_reading_after_recovery_does_not_rearm(engine):
    eng, hal, log = engine
    eng.process_reading(reading(60))
    eng.acknowledge("app")
    eng.process_reading(reading(75))
    eng.process_reading(reading(75, stale=True))
    clock.advance(REARM_MIN * 60)
    eng.tick()
    assert eng.state.state == "acknowledged"


def test_timed_out_warning_never_self_closes_on_readings_while_forecasts_stay_low(engine):
    eng, hal, log = engine
    warn(eng)
    clock.advance(PENDING_TIMEOUT_MIN * 60)
    eng.tick()
    assert eng.state.state == "active" and eng.trigger == "predicted_low"
    for _ in range(4):  # readings above 70 (the actual threshold) while the forecast is still low
        eng.process_reading(reading(80))
        eng.process_forecast(forecast(60))
    assert eng.state.state == "active" and eng.trigger == "predicted_low"
    clock.advance(10 * 60)
    eng.tick()
    escalations = [t for t in log if t.escalated]
    assert eng.state.state == "active"
    assert sum(1 for t in log if t.old_state == "idle" and t.new_state == "pending") == 1
    assert len(escalations) == 2  # the timeout to full, then the one strobe step


def test_timed_out_warning_closes_only_on_two_recovered_forecasts(engine):
    eng, hal, log = engine
    warn(eng)
    clock.advance(PENDING_TIMEOUT_MIN * 60)
    eng.tick()
    eng.process_forecast(forecast(120))
    assert eng.state.state == "active"
    eng.process_forecast(forecast(120))
    assert eng.state.state == "idle" and hal.calls[-1] == ("set_leds", "ambient")


def test_timed_out_warning_recorder_writes_one_event(engine):
    from app.rounds.alarm_events import AlarmEventRecorder
    eng, hal, log = engine
    rec = AlarmEventRecorder()
    eng.on_transition(rec)
    warn(eng)
    clock.advance(PENDING_TIMEOUT_MIN * 60)
    eng.tick()
    for _ in range(3):
        eng.process_reading(reading(80))
        eng.process_forecast(forecast(60))
        clock.advance(5 * 60)
        eng.tick()
    eng.process_forecast(forecast(120))
    eng.process_forecast(forecast(120))
    assert len(rec.events) == 1 and rec.events[0].tier == "predicted_low"
    assert rec.events[0].escalated and not rec.events[0].crossed_actual
