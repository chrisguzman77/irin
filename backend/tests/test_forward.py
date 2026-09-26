"""C1 check (Pi side): an unreachable cloud changes nothing about alarms or
staleness; batches carry everything since the last ACCEPTED batch; replay
rows are flagged is_demo; the cursor survives a restart."""

import asyncio
import json
from datetime import datetime, timedelta

import httpx
import pytest

from app import store
from app.clock import clock
from app.contracts import Reading
from app.forward import CURSOR_KEY, Forwarder

T0 = datetime(2021, 3, 1, 0, 0)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(store.config, "IRIN_DB", str(tmp_path / "t.db"))
    store.init_db()
    clock.set(speed=60.0, start=T0 + timedelta(hours=1))
    yield
    clock.reset()


def readings(since):
    rows = [Reading(timestamp=T0 + timedelta(minutes=5 * i), glucose_mgdl=100 + i, trend="Flat", source="replay")
            for i in range(12)]
    return [r.model_dump(mode="json") for r in rows if r.timestamp > since]


class Cloud:
    """A fake /v1/ingest: records batches; `fail` makes it unreachable or a 503."""

    def __init__(self, fail=None):
        self.batches = []
        self.fail = fail

    def transport(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if self.fail == "down":
                raise httpx.ConnectError("connection refused")
            if self.fail == "503":
                return httpx.Response(503, json={"detail": "storage unavailable"})
            body = json.loads(request.content)
            assert request.headers["X-Device-Id"] == "irin-test" and request.headers["X-Device-Token"] == "tok"
            self.batches.append(body)
            return httpx.Response(200, json={"stored": {k: len(v) for k, v in body.items() if isinstance(v, list)}})

        return httpx.MockTransport(handler)


def make(cloud, **kw):
    return Forwarder(readings_since=readings, cloud_url="http://cloud.test", device_id="irin-test",
                     device_token="tok", transport=cloud.transport(), **kw)


def test_unreachable_cloud_keeps_the_cursor_and_never_raises(db):
    cloud = Cloud(fail="down")
    f = make(cloud)
    assert asyncio.run(f.tick()) is False and f.failures == 1
    assert abs((f.cursor - (T0 + timedelta(hours=1) - timedelta(hours=24))).total_seconds()) < 5  # 24 h backfill
    cloud.fail = "503"
    assert asyncio.run(f.tick()) is False and f.failures == 2 and store.get_kv(CURSOR_KEY) is None
    cloud.fail = None  # the cloud comes back: everything since the cursor goes in one batch
    assert asyncio.run(f.tick()) is True and len(cloud.batches) == 1 and len(cloud.batches[0]["readings"]) == 12
    assert f.cursor == T0 + timedelta(minutes=55) and store.get_kv(CURSOR_KEY) == f.cursor.isoformat()


def test_alarm_and_staleness_never_wait_for_the_cloud():
    """The forwarder is its own task and nothing in the alarm or datasource
    path imports it (the only coupling is main.py's lifespan)."""
    import inspect

    from app import alarm, datasource, ws
    from app.datasource import nightscout, replay

    for mod in (alarm, ws, nightscout, replay):
        assert "forward" not in inspect.getsource(mod)


def test_second_tick_sends_only_new_rows_and_flags_demo(db):
    cloud = Cloud()
    f = make(cloud, is_demo=lambda: True)
    assert asyncio.run(f.tick()) is True
    assert all(r["is_demo"] is True for r in cloud.batches[0]["readings"])
    assert asyncio.run(f.tick()) is True and len(cloud.batches) == 1  # nothing new: no request
    f.readings_since = lambda since: readings(since) + [Reading(timestamp=T0 + timedelta(hours=1), glucose_mgdl=90,
                                                                trend="Flat", source="replay").model_dump(mode="json")]
    assert asyncio.run(f.tick()) is True and len(cloud.batches) == 2
    assert [r["glucose_mgdl"] for r in cloud.batches[1]["readings"]] == [90]
    assert cloud.batches[1]["device_id"] == "irin-test" and cloud.batches[1]["treatments"] == []


def test_cursor_survives_a_restart(db):
    cloud = Cloud()
    assert asyncio.run(make(cloud).tick()) is True
    f2 = make(cloud)  # a new process: the cursor is read back from the store
    assert f2.load_cursor() == T0 + timedelta(minutes=55)
    assert asyncio.run(f2.tick()) is True and len(cloud.batches) == 1


def test_disabled_without_device_credentials(db):
    f = Forwarder(readings_since=readings, cloud_url="http://cloud.test", device_id="", device_token="")
    assert f.enabled is False and asyncio.run(f.tick()) is False and f.failures == 0


def test_app_exposes_forwarder_state_and_stays_disabled_in_tests():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        s = c.get("/api/forwarder").json()
        assert set(s) == {"enabled", "cursor", "sent_batches", "failures", "last_error"}
