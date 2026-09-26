"""Step 2 check, the clock half: 15 clock minutes at 60x are 15 seconds of
wall time. Nothing here waits: asyncio.sleep and time.monotonic are patched
inside clock.py so the arithmetic is asserted, not observed."""

import asyncio
from datetime import datetime, timedelta

import pytest

from app import clock as clock_module
from app.clock import Clock


def test_fifteen_clock_minutes_at_60x_is_fifteen_wall_seconds(monkeypatch):
    requested: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        requested.append(seconds)

    monkeypatch.setattr(clock_module.asyncio, "sleep", fake_sleep)
    c = Clock()
    c.set(speed=60.0, start=datetime(2020, 1, 1, 22, 0))
    asyncio.run(c.sleep(15 * 60))  # a 15-minute re-arm timer
    assert requested == [15.0]


def test_now_advances_at_speed(monkeypatch):
    wall = [1000.0]
    monkeypatch.setattr(clock_module.time, "monotonic", lambda: wall[0])
    c = Clock()
    c.set(speed=60.0, start=datetime(2020, 1, 1, 22, 0))
    wall[0] += 15.0  # 15 s of wall time
    assert c.now() == datetime(2020, 1, 1, 22, 15)
    assert c.elapsed() == pytest.approx(900.0)
    c.advance(5 * 60)  # a seek or a test jump adds clock time on top
    assert c.now() == datetime(2020, 1, 1, 22, 20)


def test_live_mode_is_wall_time_at_1x(monkeypatch):
    wall = [50.0]
    monkeypatch.setattr(clock_module.time, "monotonic", lambda: wall[0])
    c = Clock()
    c.set(speed=60.0, start=datetime(2020, 1, 1))
    c.reset()
    assert c.speed == 1.0
    t0 = c.now()
    wall[0] += 10.0
    assert c.now() - t0 == timedelta(seconds=10)


def test_clock_never_runs_backwards_and_speed_is_positive():
    c = Clock()
    with pytest.raises(ValueError):
        c.advance(-1)
    with pytest.raises(ValueError):
        c.set(speed=0)


def test_resync_moves_now_to_wall_time_but_keeps_elapsed_continuous():
    clock = Clock()
    clock.advance(600)  # 10 min of staleness already counted
    before = clock.elapsed()
    clock._start = clock._start - timedelta(hours=5)  # the pre-NTP wall clock was hours behind
    assert clock.now() < datetime.now() - timedelta(hours=4)
    clock.resync()
    assert abs((clock.now() - datetime.now()).total_seconds()) < 1
    assert clock.elapsed() >= before and clock.elapsed() - before < 1
