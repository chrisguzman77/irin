"""Step 7 check: malformed input cannot store a number the user never
confirmed; a partial parse asks for the missing piece; the 10 s timeout
discards; parsed text never reaches shell/eval/SQL."""

from datetime import datetime

import pytest

from app import store
from app.clock import clock
from app.voice import TIMEOUT_S, VoiceLogger, parse

T0 = datetime(2020, 1, 1, 12, 0)


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    monkeypatch.setattr(store.config, "IRIN_DB", str(path))
    store.init_db(path)
    clock.set(speed=1.0, start=T0)
    yield path
    clock.reset()


def stored():
    return store.select_treatments(datetime(2000, 1, 1))


@pytest.mark.parametrize("text,carbs,units", [
    ("log 45 carbs and 5 units", 45, 5),
    ("45 grams of carbs, 5 units of insulin", 45, 5),
    ("5 units", None, 5),
    ("log 30 carbs", 30, None),
    ("carbs 20 and insulin 2.5", 20, 2.5),
    ("I had 60g and took 6u", 60, 6),
])
def test_parse_full_entries(text, carbs, units):
    r = parse(text)
    assert (r.carbs_g, r.insulin_units) == (carbs, units) and r.complete


@pytest.mark.parametrize("text,missing", [
    ("log carbs", ["carbs amount"]),
    ("log some units", ["insulin amount"]),
    ("log 45", ["unit"]),
    ("carbs and insulin", ["carbs amount", "insulin amount"]),
])
def test_partial_parse_names_the_missing_piece(text, missing):
    r = parse(text)
    assert list(r.missing) == missing and not r.complete


@pytest.mark.parametrize("text", ["", "   ", "hello there", "x" * 201, "log 500 carbs", "log 80 units"])
def test_malformed_or_absurd_is_an_error(text):
    r = parse(text)
    assert r.error and not r.complete


def test_insulin_is_echoed_and_never_stored_without_confirm(db):
    v = VoiceLogger()
    r = v.submit("log 45 carbs and 5 units")
    assert r["status"] == "needs_confirm" and r["echo"] == "45 carbs and 5 units, save?"
    assert stored() == []  # nothing yet, not even the carbs
    c = v.confirm(r["pending_id"])
    assert c["status"] == "stored"
    kinds = sorted((t.kind, t.carbs_g, t.insulin_units, t.confirmed) for t in stored())
    assert kinds == [("bolus", None, 5.0, True), ("carbs", 45.0, None, True)]


def test_carbs_only_is_stored_at_once(db):
    r = VoiceLogger().submit("log 30 carbs")
    assert r["status"] == "stored" and [t.carbs_g for t in stored()] == [30.0]


def test_timeout_discards_the_pending_entry(db):
    v = VoiceLogger()
    r = v.submit("5 units")
    clock.advance(TIMEOUT_S + 1)
    assert v.confirm(r["pending_id"])["status"] == "expired" and stored() == []


def test_confirm_inside_the_window_stores(db):
    v = VoiceLogger()
    r = v.submit("5 units")
    clock.advance(TIMEOUT_S - 1)
    assert v.confirm(r["pending_id"])["status"] == "stored" and len(stored()) == 1


def test_unknown_or_reused_pending_id_stores_nothing(db):
    v = VoiceLogger()
    r = v.submit("5 units")
    assert v.confirm("nope")["status"] == "expired"
    v.confirm(r["pending_id"])
    assert v.confirm(r["pending_id"])["status"] == "expired"  # single use
    assert len(stored()) == 1


def test_cancel_discards(db):
    v = VoiceLogger()
    r = v.submit("5 units")
    assert v.cancel(r["pending_id"])["status"] == "cancelled"
    assert v.confirm(r["pending_id"])["status"] == "expired" and stored() == []


def test_incomplete_and_error_store_nothing(db):
    v = VoiceLogger()
    assert v.submit("log units")["status"] == "incomplete"
    assert v.submit("'; DROP TABLE treatments; --")["status"] == "error"
    assert stored() == [] and store.select_treatments(datetime(2000, 1, 1)) == []  # the table survived


def test_injection_text_with_numbers_only_parses_numbers(db):
    v = VoiceLogger()
    r = v.submit("5 units'); DROP TABLE treatments; --")
    assert r["status"] == "needs_confirm" and r["echo"] == "5 units, save?"
    v.confirm(r["pending_id"])
    assert [t.insulin_units for t in stored()] == [5.0]  # regex only; the text never touched SQL


# --- the endpoints ---


@pytest.fixture
def client(db, monkeypatch):
    from fastapi.testclient import TestClient

    from app import auth
    from app.main import app

    monkeypatch.setattr(auth.config, "PIN", "1234")
    with TestClient(app) as c:
        yield c


H = {"X-PIN": "1234"}


def test_voice_endpoints_are_pin_gated(client):
    assert client.post("/api/log/voice", json={"text": "5 units"}).status_code == 401
    assert client.post("/api/log/voice/x/confirm").status_code == 401
    assert client.post("/api/log", json={"timestamp": T0.isoformat(), "kind": "carbs", "carbs_g": 10}).status_code == 401


def test_voice_endpoint_echo_confirm_round_trip(client):
    r = client.post("/api/log/voice", json={"text": "log 45 carbs and 5 units"}, headers=H).json()
    assert r["status"] == "needs_confirm" and r["echo"] == "45 carbs and 5 units, save?"
    assert stored() == []
    c = client.post(f"/api/log/voice/{r['pending_id']}/confirm", headers=H).json()
    assert c["status"] == "stored" and len(c["stored"]) == 2
    assert sorted(t.kind for t in stored()) == ["bolus", "carbs"]
    assert client.post(f"/api/log/voice/{r['pending_id']}/confirm", headers=H).json()["status"] == "expired"


def test_structured_log_refuses_unconfirmed_insulin(client):
    body = {"timestamp": T0.isoformat(), "kind": "bolus", "insulin_units": 4, "confirmed": False}
    assert client.post("/api/log", json=body, headers=H).status_code == 422
    assert stored() == []
    body["confirmed"] = True
    assert client.post("/api/log", json=body, headers=H).status_code == 200
    assert [t.insulin_units for t in stored()] == [4.0]
    assert client.get("/api/treatments?hours=48", headers=H).status_code == 200


def test_structured_log_rejects_future_timestamps(client):
    from datetime import timedelta

    body = {"timestamp": (clock.now() + timedelta(hours=2)).isoformat(), "kind": "carbs", "carbs_g": 10}
    assert client.post("/api/log", json=body, headers=H).status_code == 400  # the app's clock is replay's
