"""slavik.md step 2: LED states, distinctness, the current cap, and the frame
writer, all on a laptop (a fake strip stands in for Pi5Neo; no sleeps)."""

import math

import pytest

from hardware import hal, leds
from hardware.leds import LedFrame, frame_current_a, render_frame


class FakeStrip:
    def __init__(self) -> None:
        self.pixels: dict[int, tuple] = {}
        self.updates = 0

    def set_led_color(self, i, r, g, b):
        self.pixels[i] = (r, g, b)

    def update_strip(self, sleep_duration=0.1):
        self.updates += 1


def lit(px):
    return [p for p in px if p != (0, 0, 0)]


def test_off_is_dark():
    assert lit(render_frame("off", 5.0)) == []


def test_every_state_renders_29_pixels():
    for state in hal.LED_STATES:
        assert len(render_frame(state, 1.0)) == 29


def test_unknown_state_raises():
    with pytest.raises(ValueError):
        render_frame("rainbow", 0.0)


def test_ambient_is_dim_and_steady():
    a, b = render_frame("ambient", 0.0), render_frame("ambient", 7.3)
    assert a == b
    assert max(max(p) for p in a) <= 0.1 * 255


def test_warning_is_amber_and_ramps_up():
    early, late = render_frame("warning", 0.0)[0], render_frame("warning", leds.RAMP_S)[0]
    assert sum(early) < sum(late)
    r, g, b = late
    assert r > g > b == 0  # amber: red-dominant, some green, no blue


def test_full_is_red_and_pulses():
    samples = [render_frame("full", t)[0] for t in (0.0, 0.25, 0.5, 0.75)]
    assert all(g == 0 and b == 0 for _, g, b in samples)
    assert len({p[0] for p in samples}) > 1  # it moves


def test_warning_and_full_look_different_at_every_moment():
    for i in range(40):
        t = i * 0.5
        w, f = render_frame("warning", t)[0], render_frame("full", t)[0]
        assert w[1] > 0 and f[1] == 0  # amber carries green, red never does


def test_strobe_alternates_red_and_white():
    frames = {render_frame("strobe", t)[0] for t in (0.0, 1 / 6 + 0.01)}
    assert len(frames) == 2
    assert any(p[1] > 0 and p[2] > 0 for p in frames)  # the white half
    assert any(p[1] == 0 and p[2] == 0 for p in frames)  # the red half


def test_buddy_alert_is_a_moving_band():
    a, b = render_frame("buddy_alert", 0.0), render_frame("buddy_alert", 0.5)
    assert a != b
    bright = [p for p in a if p[2] > 100]
    assert len(bright) == 5


@pytest.mark.parametrize("state", sorted(hal.LED_STATES))
def test_current_cap_holds_for_every_state(state):
    for i in range(60):
        px = render_frame(state, i * 0.37, brightness=1.0, color=(255, 255, 255) if state == "ambient" else None)
        assert frame_current_a(px) <= leds.MAX_CURRENT_A + 1e-9


def test_full_white_strobe_is_trimmed_not_dropped():
    px = render_frame("strobe", 1 / 6 + 0.01)  # the white half
    assert frame_current_a(px) <= 1.0
    assert lit(px)


def test_brightness_and_color_overrides():
    px = render_frame("ambient", 0.0, color=(0, 0, 200), brightness=0.5)
    assert px[0] == (0, 0, 100)


def test_identical_alarm_colours_refused():
    with pytest.raises(ValueError):
        LedFrame(strip=FakeStrip(), colors={"warning": (255, 0, 0)}, start=False)
    frame = LedFrame(strip=FakeStrip(), start=False)
    with pytest.raises(ValueError):
        frame.set_state("full", color=leds.DEFAULT_COLORS["warning"])


def test_frame_writes_only_on_change_or_each_second():
    strip = FakeStrip()
    frame = LedFrame(strip=strip, start=False)
    frame.set_state("ambient")
    t0 = 1000.0
    frame._since = t0
    frame.tick(t0)
    assert strip.updates == 1 and len(strip.pixels) == 29
    frame.tick(t0 + 0.1)  # steady ambient: no rewrite
    assert strip.updates == 1
    frame.tick(t0 + 1.2)  # once-a-second refresh
    assert strip.updates == 2


def test_close_turns_the_frame_off():
    strip = FakeStrip()
    frame = LedFrame(strip=strip, start=False)
    frame.set_state("full")
    frame.tick()
    frame.close()
    assert all(p == (0, 0, 0) for p in strip.pixels.values())


def test_render_thread_starts_and_stops():
    frame = LedFrame(strip=FakeStrip(), start=True)
    frame.set_state("ambient")
    frame.close()
    assert not frame._thread.is_alive()


def test_pulse_math_sanity():
    # full at its peak (t = 0.25 s) is brighter than at its trough (t = 0.75 s)
    assert render_frame("full", 0.25)[0][0] > render_frame("full", 0.75)[0][0]
    assert math.isclose(leds.MA_PER_CHANNEL, 20.0)
