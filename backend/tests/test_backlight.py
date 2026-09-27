"""The backlight controller: dim in the night window, full by day, full
whenever a low alarm sounds, writes only on a change, never raises into the
alarm engine, and goes through the output gate (Away remembers the level)."""

from datetime import datetime

import app.config  # noqa: F401  (puts the repo root on sys.path, for hardware/)
from app.alarm import AlarmEngine
from app.backlight import DAY_LEVEL, NIGHT_LEVEL, BacklightController
from app.clock import clock
from app.contracts import AlarmState, Reading, Settings
from app.outputs import GatedOutputs
from app.presence import PresenceMachine
from hardware.mock import MockHAL


class Out:
    def __init__(self):
        self.levels = []

    def set_display_brightness(self, level):
        self.levels.append(level)


def ctl(mode="detail", alarm=None):
    out = Out()
    state = {"mode": mode, "alarm": alarm or AlarmState()}
    c = BacklightController(out, lambda: state["mode"], lambda: state["alarm"])
    return c, out, state


def test_full_by_day_dim_at_night():
    c, out, s = ctl("detail")
    c.update()
    s["mode"] = "night"
    c.update()
    s["mode"] = "morning"
    c.update()
    assert out.levels == [DAY_LEVEL, NIGHT_LEVEL, DAY_LEVEL]


def test_writes_only_on_a_change():
    c, out, s = ctl("night")
    for _ in range(5):
        c.update()
    assert out.levels == [NIGHT_LEVEL]


def test_a_sounding_low_is_always_full_even_at_night():
    for trigger in ("predicted_low", "actual_low"):
        for st in ("pending", "active", "rearmed"):
            c, out, s = ctl("night", AlarmState(state=st, trigger_type=trigger))
            c.update()
            assert out.levels == [DAY_LEVEL], (trigger, st)


def test_acknowledged_or_over_goes_back_to_night_dim():
    c, out, s = ctl("night", AlarmState(state="active", trigger_type="actual_low"))
    c.update()
    s["alarm"] = AlarmState(state="acknowledged", trigger_type="actual_low")
    c.update()
    s["alarm"] = AlarmState()
    c.update()
    assert out.levels == [DAY_LEVEL, NIGHT_LEVEL]


def test_high_and_stale_do_not_force_full():
    for trigger in ("high", "stale"):
        c, out, s = ctl("night", AlarmState(state="active", trigger_type=trigger))
        c.update()
        assert out.levels == [NIGHT_LEVEL]


def test_never_raises_into_the_alarm_engine():
    def boom():
        raise RuntimeError("scheduler broken")

    c = BacklightController(Out(), boom, lambda: AlarmState())
    assert c.update(object()) is None  # swallowed and logged


def test_wired_as_an_observer_a_real_low_brightens_the_night_screen():
    clock.set(speed=60.0, start=datetime(2020, 1, 2, 2, 0))
    try:
        settings = Settings()
        hal = MockHAL()
        out = GatedOutputs(hal, PresenceMachine(settings))
        eng = AlarmEngine(settings, hal=out)
        c = BacklightController(out, lambda: "night", lambda: eng.state)
        eng.on_transition(c.update)
        c.update()
        assert hal.brightness == NIGHT_LEVEL
        for _ in range(4):  # feed low readings until the engine alarms
            eng.process_reading(Reading(timestamp=clock.now(), glucose_mgdl=55, trend="SingleDown", source="replay"))
            if eng.state.state != "idle":
                break
            clock.advance(300)
        assert eng.state.state != "idle", "the engine never alarmed on 55 mg/dL"
        assert hal.brightness == DAY_LEVEL
    finally:
        clock.reset()


def test_away_remembers_the_level_and_applies_it_on_return():
    clock.set(speed=60.0, start=datetime(2020, 1, 1, 14, 0))
    try:
        settings = Settings()
        hal = MockHAL()
        pm = PresenceMachine(settings, away_after_min=1)
        out = GatedOutputs(hal, pm)
        pm.set_override("away")
        c = BacklightController(out, lambda: "night", lambda: AlarmState())
        c.update()
        assert hal.brightness == 1.0  # suppressed while away
        pm.set_override("home")
        assert hal.brightness == NIGHT_LEVEL  # replayed on return
    finally:
        clock.reset()
