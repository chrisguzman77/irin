"""Step 6 check: a gap in replay suspends forecasting and the status says so;
stale data is never forecast on; a clean hour yields a forecast the alarm
engine consumes; the window rule is George's latest_window, never a copy."""

import asyncio
from datetime import datetime, timedelta

import numpy as np

from app.clock import clock
from app.contracts import Reading
from app.datasource.replay import ReplayDataSource
from app.forecast import Forecaster

T0 = datetime(2020, 1, 1, 22, 0)


def readings(n: int, start: datetime = T0, stale_last: bool = False) -> list[Reading]:
    out = [Reading(timestamp=start + timedelta(minutes=5 * i), glucose_mgdl=120 - i, trend="Flat", source="replay")
           for i in range(n)]
    if stale_last:
        out[-1] = out[-1].model_copy(update={"is_stale": True})
    return out


def fake_predict(window, hour):
    return float(window[-1]) - 20.0  # a falling forecast


def test_clean_history_forecasts_from_the_newest_reading():
    f = Forecaster(predict=fake_predict)
    res = f.forecast(readings(30))  # 2.5 h of clean readings
    assert res.status == "ok" and res.forecast.predicted_mgdl == 71.0
    assert res.forecast.timestamp == T0 + timedelta(minutes=145) and res.forecast.horizon_min == 30
    assert f.last is res and res.payload()["status"] == "ok"


def test_a_dropped_reading_is_a_nan_slot_not_a_suspension():
    seen = {}

    def spy(window, hour):
        seen["window"] = np.asarray(window)
        return 100.0

    rows = readings(30)
    del rows[-3]  # one missing reading 10 min before the newest
    assert Forecaster(predict=spy).forecast(rows).status == "ok"
    assert np.isnan(seen["window"]).sum() == 1  # George's rule: NaN slot, XGBoost missing value


def test_stale_newest_reading_suspends_before_the_window_is_built():
    res = Forecaster(predict=fake_predict).forecast(readings(30, stale_last=True))
    assert res.status == "suspended" and "stale" in res.reason and res.forecast is None


def test_short_history_suspends():
    res = Forecaster(predict=fake_predict).forecast(readings(3))
    assert res.status == "suspended" and res.reason == "gap in the last hour"


def test_model_error_or_nan_never_raises():
    def boom(window, hour):
        raise RuntimeError("model exploded")

    assert "RuntimeError" in Forecaster(predict=boom).forecast(readings(30)).reason
    assert "non-finite" in Forecaster(predict=lambda w, h: float("nan")).forecast(readings(30)).reason


def test_no_model_is_unavailable_not_a_crash(monkeypatch):
    import app.forecast as fm

    monkeypatch.setattr(fm, "_load_predict", lambda: None)
    f = Forecaster()
    assert not f.available and f.forecast(readings(30)).status == "unavailable"


def test_gap_in_replay_suspends_then_resumes(tmp_path):
    """A scenario with a 45-minute hole (9 missing readings): forecasting stops
    across the hole and for the first hour of the new stretch, then resumes."""
    rows = ["timestamp,glucose_mgdl,trend"]
    for i in range(60):
        if 20 <= i < 29:
            continue
        rows.append(f"{(T0 + timedelta(minutes=5 * i)).isoformat()},{120 - i // 2},Flat")
    p = tmp_path / "gap.csv"
    p.write_text("\n".join(rows) + "\n")
    ds = ReplayDataSource(p, speed=60.0)
    f = Forecaster(predict=fake_predict)

    async def run():
        await ds.start()
        status, reason = {}, {}
        for i in range(60):
            latest = await ds.get_latest()  # the FEED's verdict on staleness
            res = f.forecast(await ds.history(minutes=75), latest=latest)
            status[i], reason[i] = res.status, res.reason
            clock.advance(5 * 60)
        await ds.stop()
        assert status[19] == "ok"  # clean hour before the hole
        assert status[20] == "ok" and status[21] == "ok"  # the last reading is under 15 min old: still fresh
        assert all(status[i] == "suspended" for i in range(22, 29))  # the feed is stale from 15 min on
        assert reason[22] == "latest reading is stale" and reason[30] == "gap in the last hour"
        assert all(status[i] == "suspended" for i in range(29, 41))  # the new stretch's first hour
        assert status[45] == "ok" and status[59] == "ok"  # resumed

    asyncio.run(run())


def test_real_model_matches_georges_check_file():
    """George's Pi check: predict(window with None -> NaN, hour) == expected within 1e-3."""
    import json
    from pathlib import Path

    import pytest

    chk = Path(__file__).resolve().parents[2] / "ml" / "models" / "forecast_v1_check.json"
    if not chk.exists():
        pytest.skip("forecast_v1_check.json not present")
    f = Forecaster()
    if not f.available:
        pytest.skip("model not loadable here (no forecast_v1.json or no OpenMP runtime)")
    from ml.models.predict import predict

    d = json.loads(chk.read_text())
    window = np.array([np.nan if v is None else v for v in d["window"]], dtype=float)
    assert abs(predict(window, d["hour"]) - d["expected"]) < 1e-3


def test_stale_feed_suspends_even_when_history_rows_look_fresh():
    """Review finding: history rows never carry is_stale; the feed's latest
    reading is the verdict, so a stale feed with a full clean window in
    history must still suspend."""
    hist = readings(30)
    stale_latest = hist[-1].model_copy(update={"is_stale": True})
    res = Forecaster(predict=fake_predict).forecast(hist, latest=stale_latest)
    assert res.status == "suspended" and res.reason == "latest reading is stale"
    p = res.payload(at=stale_latest.timestamp)
    assert p["predicted_mgdl"] is None and p["forecast"] is None and p["horizon_min"] == 30
    assert p["timestamp"] == stale_latest.timestamp.isoformat()


def test_hub_gates_the_forecast_on_the_polled_reading():
    """The hub path: a reading that flips to stale reaches the forecaster as
    suspended and the engine as no-forecast, never as a forecast."""
    from app.alarm import AlarmEngine
    from app.contracts import Settings
    from app.main import Runtime
    from app.ws import Hub
    from hardware.mock import MockHAL

    class Src:
        name = "replay"

        def __init__(self):
            self.rows = readings(30)

        async def get_latest(self):
            return self.rows[-1]

        async def history(self, minutes):
            return self.rows

        async def seek(self, to):
            raise NotImplementedError

    src = Src()
    rt = Runtime(mode="replay", datasource=src)
    rt.alarm = AlarmEngine(Settings(), hal=MockHAL())
    rt.forecaster = Forecaster(predict=lambda w, h: 50.0)  # a low forecast, if it ever got through
    hub = Hub(rt)
    sent = []
    hub.broadcast = lambda msg: _record(sent, msg)

    async def run():
        await hub._forecast(src.rows[-1])
        assert sent[-1].payload["status"] == "ok" and rt.alarm._low_forecasts == 1
        await hub._forecast(src.rows[-1].model_copy(update={"is_stale": True}))
        assert sent[-1].payload["status"] == "suspended" and sent[-1].payload["predicted_mgdl"] is None
        assert rt.alarm._low_forecasts == 0  # process_no_forecast ran; no forecast reached the engine

    asyncio.run(run())


async def _record(sent, msg):
    sent.append(msg)


def test_hub_loop_survives_a_failing_poll():
    from app.main import Runtime
    from app.ws import Hub

    class Bad:
        name = "replay"
        calls = 0

        async def get_latest(self):
            self.calls += 1
            raise RuntimeError("db exploded")

        async def history(self, minutes):
            return []

        async def seek(self, to):
            raise NotImplementedError

    src = Bad()
    hub = Hub(Runtime(mode="replay", datasource=src))

    async def run():
        hub.start()
        for _ in range(3):
            await asyncio.sleep(0)  # let the loop run a few polls (clock.sleep at 1x is real time; 0 iterations suffice)
        await hub.stop()
        assert src.calls >= 1  # it polled, raised, and the task was still alive to be stopped cleanly

    asyncio.run(run())
