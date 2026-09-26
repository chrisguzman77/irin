"""Replay serves readings in order and marks stale after a gap. Never
sleep(): drive clock.py with advance()."""

import asyncio
from datetime import timedelta
from pathlib import Path

from app.clock import clock
from app.datasource.replay import ReplayDataSource

CSV = """timestamp,glucose_mgdl,trend
2020-01-01T22:00:00,120,Flat
2020-01-01T22:05:00,118,Flat
2020-01-01T22:10:00,115,FortyFiveDown
2020-01-01T22:50:00,100,FortyFiveDown
"""


def _source(tmp_path: Path) -> ReplayDataSource:
    p = tmp_path / "s.csv"
    p.write_text(CSV)
    return ReplayDataSource(p, speed=60.0)


def test_readings_in_order_and_stale_after_gap(tmp_path):
    ds = _source(tmp_path)

    async def run():
        await ds.start()
        first = await ds.get_latest()
        assert first is not None and first.glucose_mgdl == 120 and not first.is_stale

        clock.advance(10 * 60)  # 22:10
        r = await ds.get_latest()
        assert r.glucose_mgdl == 115 and not r.is_stale
        hist = await ds.history(minutes=60)
        assert [h.glucose_mgdl for h in hist] == [120, 118, 115]

        clock.advance(20 * 60)  # 22:30: 20 min since the last reading -> stale
        r = await ds.get_latest()
        assert r.glucose_mgdl == 115 and r.is_stale

        clock.advance(20 * 60)  # 22:50: a fresh reading arrives
        r = await ds.get_latest()
        assert r.glucose_mgdl == 100 and not r.is_stale
        await ds.stop()

    asyncio.run(run())


def test_replay_sets_clock_to_scenario_time(tmp_path):
    ds = _source(tmp_path)

    async def run():
        await ds.start()
        assert clock.now().year == 2020 and clock.speed == 60.0
        await ds.stop()
        assert clock.speed == 1.0

    asyncio.run(run())


def test_the_save_plays_end_to_end_at_60x():
    """The shipped scenario, whatever George cuts it to: every row is served in
    order at its own timestamp, fresh, and the feed turns stale 15 clock
    minutes after the CSV ends."""
    from app.config import config

    ds = ReplayDataSource(config.scenario_path, speed=60.0)
    rows = ds.rows
    assert len(rows) >= 24 and ds.speed == 60.0  # at least two hours of night

    async def run():
        await ds.start()
        seen = []
        for ts, mgdl, _trend in rows:
            clock.advance(max(0.0, (ts - clock.now()).total_seconds()))  # to this row, exactly
            r = await ds.get_latest()
            assert r is not None and r.timestamp == ts and r.glucose_mgdl == mgdl
            assert r.source == "replay" and not r.is_stale
            seen.append(r)
        assert len(seen) == len(rows)
        assert all(a.timestamp < b.timestamp for a, b in zip(seen, seen[1:]))
        assert min(r.glucose_mgdl for r in seen) == min(g for _, g, _ in rows) < 70  # The Save has its low

        hist = await ds.history(minutes=60)
        assert hist[-1].timestamp == seen[-1].timestamp
        assert len(hist) == sum(1 for ts, _, _ in rows if ts >= clock.now() - timedelta(minutes=60))
        assert all(not h.is_stale for h in hist)  # history rows are facts, never stale

        clock.advance(15 * 60)  # 15 min since the last row -> stale, honestly
        last = await ds.get_latest()
        assert last.timestamp == seen[-1].timestamp and last.is_stale
        await ds.stop()

    asyncio.run(run())
