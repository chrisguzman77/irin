"""Step 9 check: drive the mock through day/night transitions and assert the
night rule (radar absence alone never sets Away at night), the toggle
override, that None is no evidence, and that Away gates room outputs only
(the alarm engine keeps running and a still-active alarm sounds the moment
presence returns)."""

from datetime import datetime

import pytest

from app.alarm import AlarmEngine
from app.clock import clock
from app.contracts import Reading, Settings
from app.outputs import GatedOutputs
from app.presence import AWAY_AFTER_MIN, PresenceMachine
from hardware.mock import MockHAL

DAY = datetime(2020, 1, 1, 14, 0)
NIGHT = datetime(2020, 1, 2, 2, 0)


@pytest.fixture
def pm():
    clock.set(speed=60.0, start=DAY)
    settings = Settings()
    yield PresenceMachine(settings), settings
    clock.reset()


def absent_for(pm, minutes: int, step: int = 1):
    """Sustained absence: a False sample now, then one every `step` minutes,
    the last one exactly `minutes` after the first."""
    pm.sample(False)
    for _ in range(0, minutes, step):
        clock.advance(step * 60)
        pm.sample(False)


def test_home_on_any_detection_instantly(pm):
    m, _ = pm
    absent_for(m, AWAY_AFTER_MIN + 5)
    assert m.state.mode == "away"
    m.sample(True)
    assert m.state.mode == "home" and m.state.source == "radar"


def test_away_only_after_15_min_sustained_absence_by_day(pm):
    m, _ = pm
    absent_for(m, AWAY_AFTER_MIN - 1)
    assert m.state.mode == "home"
    absent_for(m, 1)
    assert m.state.mode == "away"


def test_a_detection_restarts_the_absence_timer(pm):
    m, _ = pm
    absent_for(m, 10)
    m.sample(True)
    absent_for(m, 10)
    assert m.state.mode == "home"  # only 10 min since the last detection


def test_radar_absence_alone_never_sets_away_at_night(pm):
    m, _ = pm
    clock.set(speed=60.0, start=NIGHT)
    absent_for(m, 4 * 60, step=5)  # four hours of an "empty" room at night (02:00-06:00)
    assert m.state.mode == "home"
    clock.set(speed=60.0, start=datetime(2020, 1, 2, 7, 0))  # the window ends
    m.sample(False)
    assert m.state.mode == "home"  # overnight absence never counts the instant the window closes
    absent_for(m, AWAY_AFTER_MIN - 1)
    assert m.state.mode == "home"
    absent_for(m, 1)
    assert m.state.mode == "away"  # 15 fresh daytime minutes


def test_toggle_always_wins(pm):
    m, settings = pm
    m.set_override("away")
    m.sample(True)  # the radar sees someone; the toggle still wins
    assert m.state.mode == "away" and m.state.source == "toggle"
    m.set_override("home")
    absent_for(m, 60)
    assert m.state.mode == "home" and m.state.source == "toggle"
    m.set_override("auto")
    assert m.state.mode == "home" and m.state.source == "radar"
    absent_for(m, AWAY_AFTER_MIN)
    assert m.state.mode == "away"  # radar back in charge


def test_override_changed_behind_the_machines_back_is_honored(pm):
    m, settings = pm
    settings.presence_override = "away"  # a future /api/settings write, not set_override
    m.sample(True)
    assert m.state.mode == "away" and m.state.source == "toggle"
    absent_for(m, 30)
    settings.presence_override = "auto"
    m.sample(False)
    assert m.state.mode == "home"  # the stale timer was cleared, not honored
    absent_for(m, AWAY_AFTER_MIN)
    assert m.state.mode == "away"


def test_toggle_away_works_at_night_too(pm):
    m, _ = pm
    clock.set(speed=60.0, start=NIGHT)
    m.set_override("away")
    assert m.state.mode == "away"  # the toggle is a human decision; the night rule is about the radar


def test_none_is_no_evidence(pm):
    m, _ = pm
    for _ in range(60):
        clock.advance(60)
        m.sample(None)
    assert m.state.mode == "home"
    absent_for(m, 10)
    for _ in range(20):  # radar errors in the middle of an absence never count toward Away
        clock.advance(60)
        m.sample(None)
    assert m.state.mode == "home"
    m.sample(False)  # 30 min since the first absent sample, deciding sample is a real False
    assert m.state.mode == "away"


def test_invalid_override_rejected(pm):
    m, _ = pm
    with pytest.raises(ValueError):
        m.set_override("maybe")


def test_observers_see_changes_once(pm):
    m, _ = pm
    seen = []
    m.on_change(seen.append)
    m.sample(True)
    m.sample(True)
    absent_for(m, AWAY_AFTER_MIN)
    absent_for(m, 5)
    assert [s.mode for s in seen] == ["away"]


# --- the output gate ---


@pytest.fixture
def gated(pm):
    m, settings = pm
    hal = MockHAL()
    out = GatedOutputs(hal, m)
    eng = AlarmEngine(settings, hal=out)
    hal.calls.clear()
    return m, hal, out, eng


def reading(mgdl: float) -> Reading:
    return Reading(timestamp=clock.now(), glucose_mgdl=mgdl, trend="Flat", source="replay")


def test_away_suppresses_room_outputs_only_and_return_replays_them(gated):
    m, hal, out, eng = gated
    absent_for(m, AWAY_AFTER_MIN)
    assert m.state.mode == "away" and ("set_leds", "off") in hal.calls
    hal.calls.clear()
    eng.process_reading(reading(60))  # a full alarm while nobody is in the room
    assert eng.state.state == "active" and eng.trigger == "actual_low"  # alarm logic untouched
    assert [c for c in hal.calls if c[0] in ("play_sound", "set_leds")] == []  # nothing in the room
    m.sample(True)  # someone walks in
    assert ("set_leds", "full") in hal.calls and ("play_sound", "alarm_urgent", 1.0) in hal.calls


def test_escalation_while_away_replays_the_strobe_on_return(gated):
    from app.alarm import ESCALATION_MIN

    m, hal, out, eng = gated
    absent_for(m, AWAY_AFTER_MIN)
    eng.process_reading(reading(60))
    clock.advance(ESCALATION_MIN * 60)
    eng.tick()
    hal.calls.clear()
    m.sample(True)
    assert ("set_leds", "strobe") in hal.calls and ("play_sound", "alarm_urgent", 1.0) in hal.calls


def test_ack_while_away_clears_the_remembered_sound(gated):
    m, hal, out, eng = gated
    absent_for(m, AWAY_AFTER_MIN)
    eng.process_reading(reading(60))
    eng.acknowledge("app")
    hal.calls.clear()
    m.sample(True)
    assert not any(c[0] == "play_sound" for c in hal.calls)  # nothing to replay after the ack
    assert ("set_leds", "ambient") in hal.calls


def test_raw_presence_passes_through_ungated(gated):
    m, hal, out, eng = gated
    hal.set_presence_for_test(True)
    absent_for(m, AWAY_AFTER_MIN)
    assert out.get_presence() is True  # R2 reads the radar, never the gate


def test_brightness_is_gated_and_replayed(gated):
    m, hal, out, eng = gated
    absent_for(m, AWAY_AFTER_MIN)
    out.set_display_brightness(0.2)
    assert hal.brightness == 1.0
    m.sample(True)
    assert hal.brightness == 0.2
