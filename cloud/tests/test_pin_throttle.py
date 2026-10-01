"""The PIN throttle: 5 wrong PINs in 10 minutes lock a client for 10 minutes (429),
a correct PIN resets the count. No database needed: the PIN gate runs before any query."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main  # noqa: E402
from main import app  # noqa: E402

URL = "/v1/family/bearers"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "PIN", "1234")
    main._pin_wrong.clear()
    main._pin_locked_until.clear()
    clock = {"t": 1000.0}
    monkeypatch.setattr(main.time, "monotonic", lambda: clock["t"])
    with TestClient(app) as c:
        c.clock = clock
        yield c


def bad(c, ip="9.9.9.9", pin="0000"):
    return c.post(URL, json={}, headers={"X-PIN": pin, "X-Forwarded-For": f"{ip}, 10.0.0.1"})


def test_fifth_wrong_pin_locks_for_ten_minutes_even_for_the_right_one(client):
    assert [bad(client).status_code for _ in range(5)] == [401] * 5
    r = bad(client, pin="1234")
    assert r.status_code == 429 and r.json() == {"detail": "too many PIN attempts; wait 10 minutes"}
    client.clock["t"] += 599
    assert bad(client, pin="1234").status_code == 429
    client.clock["t"] += 2
    assert bad(client).status_code == 401  # the lock is over


def test_other_clients_are_not_locked(client):
    for _ in range(5):
        bad(client)
    assert bad(client, ip="8.8.8.8").status_code == 401


def test_a_correct_pin_resets_the_count(client):
    for _ in range(4):
        assert bad(client).status_code == 401
    assert bad(client, pin="1234").status_code != 401  # passes the gate (body is then judged on its own)
    assert [bad(client).status_code for _ in range(4)] == [401] * 4


def test_wrong_pins_outside_the_window_do_not_add_up(client):
    for _ in range(4):
        bad(client)
    client.clock["t"] += 601
    assert [bad(client).status_code for _ in range(4)] == [401] * 4
