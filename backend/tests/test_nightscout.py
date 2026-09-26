"""Step 3 checks with a fake fetch and clock.advance, never sleeping and never
the network: staleness is monotonic elapsed time since the last NEW reading,
a wall clock a day off changes nothing, the cache is served stale on boot."""

import asyncio
from datetime import datetime, timedelta

import pytest

from app import store
from app.clock import clock
from app.datasource.nightscout import NightscoutDataSource, entry_to_reading


def entry(ts: datetime, sgv: float, direction: str = "Flat") -> dict:
    return {"date": int(ts.timestamp() * 1000), "sgv": sgv, "direction": direction, "type": "sgv"}


class FakeNightscout:
    """Returns the queued entries; raises when `down` (the network killed)."""

    def __init__(self) -> None:
        self.entries: list[dict] = []
        self.down = False
        self.calls = 0

    async def __call__(self) -> list[dict]:
        self.calls += 1
        if self.down:
            raise ConnectionError("network unreachable")
        return list(self.entries)


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    monkeypatch.setattr(store.config, "IRIN_DB", str(path))
    store.init_db(path)
    return path


@pytest.fixture
def source(db):
    fake = FakeNightscout()
    ds = NightscoutDataSource("https://ns.example", "tok", fetch=fake)
    return ds, fake


def test_entry_parsing():
    r = entry_to_reading({"date": 1577919600000, "sgv": 128, "direction": "FortyFiveDown"})
    assert r.glucose_mgdl == 128 and r.trend == "FortyFiveDown" and r.source == "nightscout"
    assert entry_to_reading({"sgv": "x"}) is None


def test_network_killed_goes_stale_after_15_min(source):
    ds, fake = source
    t = datetime(2020, 1, 1, 22, 0)
    fake.entries = [entry(t, 120), entry(t + timedelta(minutes=5), 118)]

    async def run():
        clock.reset()
        assert await ds.poll_once() is True
        r = await ds.get_latest()
        assert r.glucose_mgdl == 118 and not r.is_stale

        fake.down = True  # kill the network
        clock.advance(14 * 60)
        assert await ds.poll_once() is False
        assert not (await ds.get_latest()).is_stale  # 14 min: still fresh

        clock.advance(60)  # 15 min since the last NEW reading
        r = await ds.get_latest()
        assert r.glucose_mgdl == 118 and r.is_stale  # shown as stale, never hidden

        fake.down = False  # network back, but Nightscout has nothing new
        assert await ds.poll_once() is False
        assert (await ds.get_latest()).is_stale  # a repeated poll never resets the timer

        fake.entries.append(entry(t + timedelta(minutes=10), 115))
        assert await ds.poll_once() is True
        assert not (await ds.get_latest()).is_stale

    asyncio.run(run())


def test_wall_clock_a_day_off_does_not_affect_staleness(source):
    ds, fake = source
    yesterday = datetime.now() - timedelta(days=1)  # the Pi's clock is a day ahead of the reading
    tomorrow = datetime.now() + timedelta(days=1)  # or a day behind it

    async def run():
        clock.reset()
        fake.entries = [entry(yesterday, 130)]
        assert await ds.poll_once() is True
        assert not (await ds.get_latest()).is_stale  # just arrived: fresh, whatever its timestamp says

        fake.entries = [entry(tomorrow, 131)]
        assert await ds.poll_once() is True
        assert not (await ds.get_latest()).is_stale
        clock.advance(15 * 60)
        assert (await ds.get_latest()).is_stale  # and it ages by elapsed time, not by its timestamp

    asyncio.run(run())


def test_cached_reading_is_served_stale_on_boot_until_a_fresh_poll(source):
    ds, fake = source
    t = datetime(2020, 1, 1, 22, 0)
    store.insert_reading(entry_to_reading(entry(t, 140)))

    async def run():
        await ds.start()
        try:
            r = await ds.get_latest()
            assert r.glucose_mgdl == 140 and r.is_stale
            fake.entries = [entry(t + timedelta(minutes=5), 138)]
            assert await ds.poll_once() is True
            assert not (await ds.get_latest()).is_stale
        finally:
            await ds.stop()

    asyncio.run(run())


def test_history_anchors_on_the_latest_reading_and_reads_the_cache(source):
    ds, fake = source
    t = datetime(2020, 1, 1, 22, 0)
    fake.entries = [entry(t + timedelta(minutes=5 * i), 100 + i) for i in range(13)]

    async def run():
        clock.reset()
        await ds.poll_once()
        hist = await ds.history(minutes=30)
        assert [h.glucose_mgdl for h in hist] == [106, 107, 108, 109, 110, 111, 112]
        assert all(not h.is_stale for h in hist)

    asyncio.run(run())


def test_mode_switch_to_nightscout_now_succeeds(db, monkeypatch):
    from fastapi.testclient import TestClient

    from app import main as m

    monkeypatch.setattr(m.config, "PIN", "1234")
    monkeypatch.setattr(m.config, "NIGHTSCOUT_URL", "https://ns.invalid")
    with TestClient(m.app) as c:
        r = c.post("/api/mode", json={"mode": "nightscout"}, headers={"X-PIN": "1234"})
        assert r.status_code == 200 and r.json() == {"mode": "nightscout", "changed": True}
        assert c.get("/api/health").json()["datasource"] == "nightscout"
        assert c.get("/api/latest").status_code == 404  # nothing cached, poll fails on a bad host: honest 404
        r = c.post("/api/mode", json={"mode": "replay"}, headers={"X-PIN": "1234"})
        assert r.status_code == 200 and c.get("/api/latest").json()["source"] == "replay"
