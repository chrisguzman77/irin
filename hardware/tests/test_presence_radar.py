"""slavik.md step 4: the raw radar reader, on a laptop (a fake pin stands in
for gpiozero's DigitalInputDevice)."""

from datetime import datetime, timedelta

import pytest

from hardware import presence
from hardware.presence import Radar


class FakePin:
    def __init__(self, value=0):
        self.value = value
        self.when_activated = None
        self.when_deactivated = None
        self.closed = False

    def close(self):
        self.closed = True


class BrokenPin(FakePin):
    @property
    def value(self):
        raise OSError("lgpio: bad read")

    @value.setter
    def value(self, _):
        pass


def ticking_clock():
    t = [datetime(2020, 1, 1, 22, 0, 0)]

    def now():
        t[0] += timedelta(seconds=1)
        return t[0]

    return now


def test_reads_raw_true_and_false():
    pin = FakePin(0)
    r = Radar(device=pin)
    assert r.read() is False
    pin.value = 1
    assert r.read() is True


def test_read_error_is_none_never_a_guess():
    r = Radar(device=BrokenPin())
    assert r.read() is None
    assert list(r.transitions) == []


def test_transitions_are_timestamped_and_deduplicated():
    pin = FakePin(0)
    r = Radar(device=pin, now=ticking_clock())
    pin.when_activated()
    pin.when_activated()  # bounce: same value, not a new transition
    pin.when_deactivated()
    states = [v for _, v in r.transitions]
    assert states == [False, True, False]
    times = [ts for ts, _ in r.transitions]
    assert times == sorted(times) and r.last_change == times[-1]


def test_real_pin_config_has_pull_down_and_debounce(monkeypatch):
    seen = {}

    class FakeDevice(FakePin):
        def __init__(self, pin, pull_up, bounce_time):
            super().__init__(0)
            seen.update(pin=pin, pull_up=pull_up, bounce_time=bounce_time)

    monkeypatch.setattr(presence, "DigitalInputDevice", FakeDevice)
    Radar()
    assert seen == {"pin": 17, "pull_up": False, "bounce_time": presence.BOUNCE_S}


def test_no_gpiozero_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(presence, "DigitalInputDevice", None)
    with pytest.raises(RuntimeError):
        Radar()


def test_close_releases_the_pin():
    pin = FakePin()
    Radar(device=pin).close()
    assert pin.closed
