"""C1 check (Pi side): an unreachable cloud changes nothing about alarms or
staleness; batches carry everything since the last ACCEPTED batch in
insertion order (a late, older reading is never skipped); replay rows are
flagged is_demo and restart per scenario run; a demo-logged treatment never
forwards as real; the cursors survive a restart; a rejected batch is skipped
instead of retried forever."""

import asyncio
import json
from datetime import datetime, timedelta

import httpx
import pytest

from app import store
from app.clock import clock
from app.contracts import Reading, Treatment
from app.forward import Forwarder

T0 = datetime(2021, 3, 1, 0, 0)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(store.config, "IRIN_DB", str(tmp_path / "t.db"))
    store.init_db()
    clock.set(speed=60.0, start=T0 + timedelta(hours=1))
    yield
    clock.reset()


def cache(*minutes):
    for m in minutes:
        store.insert_reading(Reading(timestamp=T0 + timedelta(minutes=m), glucose_mgdl=100 + m, trend="Flat",
                                     source="nightscout"))


class Cloud:
    """A fake /v1/ingest: records batches; `fail` makes it unreachable, a 503, or a 422."""

    def __init__(self, fail=None):
        self.batches = []
        self.fail = fail

    def transport(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if self.fail == "down":
                raise httpx.ConnectError("connection refused")
            if self.fail == "503":
                return httpx.Response(503, json={"detail": "storage unavailable"})
            if self.fail == "422":
                return httpx.Response(422, json={"detail": "bad row"})
            body = json.loads(request.content)
            assert request.headers["X-Device-Id"] == "irin-test" and request.headers["X-Device-Token"] == "tok"
            self.batches.append(body)
            return httpx.Response(200, json={"stored": {k: len(v) for k, v in body.items() if isinstance(v, list)}})

        return httpx.MockTransport(handler)


def make(cloud, **kw):
    return Forwarder(readings=store.select_reading_rows, treatments=store.select_treatment_rows,
                     cloud_url="http://cloud.test", device_id="irin-test", device_token="tok",
                     transport=cloud.transport(), **kw)


def mgdl(batch):
    return [r["glucose_mgdl"] for r in batch["readings"]]


def test_unreachable_cloud_keeps_the_cursors_and_never_raises(db):
    cache(0, 5, 10)
    cloud = Cloud(fail="down")
    f = make(cloud)
    assert asyncio.run(f.tick()) is False and f.failures == 1 and f.cursors == {"readings": 0, "treatments": 0,
                                                                                 "alarm_events": 0, "low_events": 0}
    cloud.fail = "503"
    assert asyncio.run(f.tick()) is False and f.failures == 2 and store.get_kv("forward_rowid:readings") is None
    cloud.fail = None  # the cloud comes back: everything since the cursor goes in one batch
    assert asyncio.run(f.tick()) is True and len(cloud.batches) == 1 and mgdl(cloud.batches[0]) == [100, 105, 110]
    assert f.cursors["readings"] == 3 and store.get_kv("forward_rowid:readings") == "3"


def test_alarm_and_staleness_never_wait_for_the_cloud():
    """The forwarder is its own task and nothing in the alarm or datasource
    path imports it (the only coupling is main.py's lifespan)."""
    import inspect

    from app import alarm, ws
    from app.datasource import nightscout, replay

    for mod in (alarm, ws, nightscout, replay):
        assert "forward" not in inspect.getsource(mod)


def test_a_late_older_reading_is_still_forwarded(db):
    """Nightscout backfills arrive after newer rows and after a treatment; a
    timestamp cursor would skip them forever, insertion order does not."""
    cache(10)
    store.insert_treatment(Treatment(timestamp=T0 + timedelta(minutes=12), kind="carbs", carbs_g=15, confirmed=True))
    cloud = Cloud()
    f = make(cloud)
    assert asyncio.run(f.tick()) is True and mgdl(cloud.batches[0]) == [110]
    cache(5)  # the 00:05 entry lands after the 00:10 one and after the 00:12 carbs
    assert asyncio.run(f.tick()) is True and mgdl(cloud.batches[1]) == [105] and cloud.batches[1]["treatments"] == []
    assert asyncio.run(f.tick()) is True and len(cloud.batches) == 2  # nothing new: no request


def test_replay_rows_are_demo_and_restart_per_scenario_run(db):
    runs = {"key": object()}
    rows = [Reading(timestamp=T0 + timedelta(minutes=5 * i), glucose_mgdl=90 + i, trend="Flat", source="replay")
            for i in range(3)]

    def replay(cursor):
        return runs["key"], [r.model_dump(mode="json") for r in rows if cursor is None or r.timestamp > cursor]

    cloud = Cloud()
    f = make(cloud, replay_readings=replay)
    assert asyncio.run(f.tick()) is True and mgdl(cloud.batches[0]) == [90, 91, 92]
    assert all(r["is_demo"] is True for r in cloud.batches[0]["readings"])
    assert asyncio.run(f.tick()) is True and len(cloud.batches) == 1  # the same run: nothing new
    runs["key"] = object()  # scenario re-selected (or live -> demo): the run replays from its first row
    assert asyncio.run(f.tick()) is True and mgdl(cloud.batches[1]) == [90, 91, 92]
    assert f.replay_cursor == T0 + timedelta(minutes=10)


def test_live_readings_after_a_demo_are_not_skipped(db):
    """The 2021 scenario and the 2026 live cache share nothing: rowid cursors for
    the cache, a per-run timestamp cursor for replay."""
    cloud = Cloud()
    key = object()
    demo = [Reading(timestamp=T0, glucose_mgdl=80, trend="Flat", source="replay").model_dump(mode="json")]
    f = make(cloud, replay_readings=lambda cursor: (key, demo if cursor is None else []))
    assert asyncio.run(f.tick()) is True and mgdl(cloud.batches[0]) == [80]
    f.replay_readings = lambda cursor: (None, [])  # back to live
    store.insert_reading(Reading(timestamp=datetime(2026, 9, 26, 12, 0), glucose_mgdl=130, trend="Flat", source="nightscout"))
    assert asyncio.run(f.tick()) is True and mgdl(cloud.batches[1]) == [130]
    assert cloud.batches[1]["readings"][0]["is_demo"] is False


def test_demo_logged_treatment_never_forwards_as_real(db):
    store.insert_treatment(Treatment(timestamp=T0, kind="carbs", carbs_g=20, confirmed=True), is_demo=True)
    store.insert_treatment(Treatment(timestamp=T0 + timedelta(minutes=1), kind="carbs", carbs_g=30, confirmed=True))
    cloud = Cloud()
    assert asyncio.run(make(cloud).tick()) is True
    flags = {t["carbs_g"]: t["is_demo"] for t in cloud.batches[0]["treatments"]}
    assert flags == {20.0: True, 30.0: False}


def test_cursors_survive_a_restart(db):
    cache(0, 5)
    cloud = Cloud()
    assert asyncio.run(make(cloud).tick()) is True
    cache(10)
    f2 = make(cloud)  # a new process: the cursors are read back from the store
    assert asyncio.run(f2.tick()) is True and mgdl(cloud.batches[1]) == [110]


def test_rejected_batch_is_skipped_not_retried_forever(db):
    cache(0)
    cloud = Cloud(fail="422")
    f = make(cloud)
    assert asyncio.run(f.tick()) is False and f.dropped_batches == 1 and f.cursors["readings"] == 1
    cloud.fail = None
    cache(5)
    assert asyncio.run(f.tick()) is True and mgdl(cloud.batches[0]) == [105]  # the rejected row is not resent


def test_disabled_without_device_credentials(db):
    f = Forwarder(readings=store.select_reading_rows, cloud_url="http://cloud.test", device_id="", device_token="")
    assert f.enabled is False and asyncio.run(f.tick()) is False and f.failures == 0


def test_app_exposes_forwarder_state_and_stays_disabled_in_tests():
    from fastapi.testclient import TestClient

    from app import main
    from app.main import app

    with TestClient(app) as c:
        s = c.get("/api/forwarder").json()
        assert s["enabled"] is False and s["sent_batches"] == 0
        # the replay source's rows are what a demo run forwards, from the first row
        run, rows = main._replay_rows(None)
        assert run is main.runtime.datasource and rows and rows[0]["source"] == "replay"
        later_run, later = main._replay_rows(datetime.fromisoformat(rows[-1]["timestamp"]))
        assert later_run is run and later == []
