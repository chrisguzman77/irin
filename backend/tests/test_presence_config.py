"""The configurable Away delay (AWAY_AFTER_MIN) and radar sample cadence
(PRESENCE_SAMPLE_SECONDS) used by the kiosk idle screen. The default stays
the spec's 15 minutes, and a shortened delay keeps every other presence rule:
never Away at night, None is no evidence, Home instantly on detection."""

from datetime import datetime

import pytest

from app import config as config_mod
from app.clock import clock
from app.contracts import Settings
from app.presence import AWAY_AFTER_MIN, PresenceMachine

DAY = datetime(2020, 1, 1, 14, 0)
NIGHT = datetime(2020, 1, 2, 2, 0)


@pytest.fixture(autouse=True)
def reset_clock():
    yield
    clock.reset()


def absent(m: PresenceMachine, seconds: int, step: int = 5) -> None:
    m.sample(False)
    for _ in range(0, seconds, step):
        clock.advance(step)
        m.sample(False)


def test_default_is_still_fifteen_minutes():
    assert AWAY_AFTER_MIN == 15
    clock.set(speed=60.0, start=DAY)
    m = PresenceMachine(Settings())
    assert m.away_after_min == 15
    absent(m, 14 * 60, step=60)
    assert m.state.mode == "home"
    absent_more = 2 * 60
    for _ in range(0, absent_more, 60):
        clock.advance(60)
        m.sample(False)
    assert m.state.mode == "away"


def test_one_minute_away_after_a_minute_not_before():
    clock.set(speed=60.0, start=DAY)
    m = PresenceMachine(Settings(), away_after_min=1)
    absent(m, 55)
    assert m.state.mode == "home"
    clock.advance(5)
    m.sample(False)
    assert m.state.mode == "away"


def test_one_minute_home_instantly_on_detection():
    clock.set(speed=60.0, start=DAY)
    m = PresenceMachine(Settings(), away_after_min=1)
    absent(m, 70)
    assert m.state.mode == "away"
    m.sample(True)
    assert m.state.mode == "home"


def test_one_minute_never_away_at_night():
    clock.set(speed=60.0, start=NIGHT)
    m = PresenceMachine(Settings(), away_after_min=1)
    absent(m, 30 * 60, step=30)
    assert m.state.mode == "home"


def test_one_minute_none_is_no_evidence():
    clock.set(speed=60.0, start=DAY)
    m = PresenceMachine(Settings(), away_after_min=1)
    m.sample(None)
    for _ in range(12):
        clock.advance(10)
        m.sample(None)
    assert m.state.mode == "home"


@pytest.mark.parametrize(
    "raw, expected",
    [(None, 15.0), ("", 15.0), ("1", 1.0), ("0.5", 0.5), ("0", 15.0), ("-3", 15.0), ("nan", 15.0), ("inf", 15.0), ("abc", 15.0)],
)
def test_away_after_min_parsing(raw, expected):
    assert config_mod._positive(raw, 15.0, floor=0.5) == expected


def test_load_config_reads_both(monkeypatch):
    monkeypatch.setenv("AWAY_AFTER_MIN", "1")
    monkeypatch.setenv("PRESENCE_SAMPLE_SECONDS", "2")
    c = config_mod.load_config()
    assert c.AWAY_AFTER_MIN == 1.0 and c.PRESENCE_SAMPLE_SECONDS == 2.0


def test_load_config_defaults(monkeypatch):
    monkeypatch.delenv("AWAY_AFTER_MIN", raising=False)
    monkeypatch.delenv("PRESENCE_SAMPLE_SECONDS", raising=False)
    c = config_mod.load_config()
    assert c.AWAY_AFTER_MIN == 15.0 and c.PRESENCE_SAMPLE_SECONDS == 30.0
