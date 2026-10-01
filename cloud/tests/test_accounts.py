"""Phone-only accounts, Phase 1 (relay/README.md "Phone-only accounts", cloud
part): POST /v1/accounts/verify checks the feed once (fake Nightscout), tells
the relay (fake relay, never a live one), and stores the feed encrypted with a
fresh dashboard token's hash; the glucose value it read never reaches a log
line or the response. GET /v1/dash/{name} resolves three credentials to the
device it draws. The phone_accounts table lives in a throwaway schema on
TIGER_URI (cloud/sql/007 applied verbatim); those tests skip when it is down."""

import hashlib
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

CLOUD = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CLOUD))
import accounts  # noqa: E402
import dash as dash_mod  # noqa: E402
import main  # noqa: E402
from main import app  # noqa: E402

NS = "https://ns.example"
RELAY = "http://relay.test"
SGV = 777  # a value no log line or response may carry
BODY = {"user_id": "u-abc123", "user_bearer": "phone-bearer-1", "nightscout_url": NS, "nightscout_token": "ns-tok"}


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


class Fake:
    """One MockTransport playing Nightscout and the relay; every knob is an attribute."""

    def __init__(self):
        self.ns_status = 200
        self.ns_age_ms = 60_000           # the newest entry is a minute old
        self.ns_body = None               # overrides the entries list when set
        self.ns_down = False
        self.relay_status = 200
        self.relay_down = False
        self.pair_ok = True
        self.calls: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        url = str(request.url)
        if url.startswith(NS):
            if self.ns_down:
                raise httpx.ConnectError("down")
            assert request.url.path == "/api/v1/entries.json"
            assert request.url.params["count"] == "1" and request.url.params["token"] == "ns-tok"
            if self.ns_status != 200:
                return httpx.Response(self.ns_status, json={"status": self.ns_status})
            if self.ns_body is not None:
                return httpx.Response(200, json=self.ns_body)
            return httpx.Response(200, json=[{"sgv": SGV, "date": int(time.time() * 1000) - self.ns_age_ms, "direction": "Flat"}])
        if url.startswith(RELAY):
            if self.relay_down:
                raise httpx.ConnectError("down")
            assert request.headers["x-cloud-key"] == "cloud-key"
            if request.url.path == "/v0/device/pair/check":
                body = json.loads(request.content)
                assert set(body) == {"device_id", "token"}
                return httpx.Response(200, json={"ok": self.pair_ok, "username": "chris" if self.pair_ok else None})
            assert request.url.path == f"/v0/users/{BODY['user_id']}/verified"
            assert request.headers["authorization"] == f"Bearer {BODY['user_bearer']}"
            assert json.loads(request.content) == {}
            if self.relay_status != 200:
                return httpx.Response(self.relay_status, json={"detail": "x"})
            return httpx.Response(200, json={"user_id": BODY["user_id"], "cgm_verified": True, "verified_at": "2026-10-01T00:00:00Z"})
        raise AssertionError(f"unexpected outbound call {url}")


@pytest.fixture
def fake(monkeypatch):
    f = Fake()
    monkeypatch.setattr(accounts, "TRANSPORT", httpx.MockTransport(f.handler))
    monkeypatch.setattr(accounts, "RELAY_URL", RELAY)
    monkeypatch.setattr(accounts, "RELAY_CLOUD_KEY", "cloud-key")
    monkeypatch.setattr(accounts, "RELAY_KEY", "relay-key")
    accounts._pair_cache.clear()
    main._verify_hits.clear()
    return f


@pytest.fixture
def scratch(monkeypatch):
    import psycopg

    uri = os.environ.get("TIGER_URI", dash_mod.TIGER_URI)
    schema = f"accounts_test_{uuid.uuid4().hex[:8]}"
    try:
        conn = psycopg.connect(uri, connect_timeout=3)
    except Exception as e:  # no database here: the check needs the compose timescaledb
        pytest.skip(f"TIGER_URI unreachable ({type(e).__name__})")
    with conn:
        conn.execute(f"CREATE SCHEMA {schema}")
        conn.execute(f"SET search_path TO {schema}")
        conn.execute((CLOUD / "sql" / "007_phone_accounts.sql").read_text())
    conn.close()
    sep = "&" if "?" in uri else "?"
    scoped = f"{uri}{sep}options=-c%20search_path%3D{schema}"
    monkeypatch.setattr(dash_mod, "TIGER_URI", scoped)
    yield scoped
    with psycopg.connect(uri) as conn:
        conn.execute(f"DROP SCHEMA {schema} CASCADE")


def row(uri, user_id):
    import psycopg

    with psycopg.connect(uri) as conn:
        return conn.execute("SELECT nightscout_url_enc, nightscout_token_enc, verified_at, dash_token_hash"
                            " FROM phone_accounts WHERE user_id = %s", (user_id,)).fetchone()


def verify(c, **over):
    return c.post("/v1/accounts/verify", json={**BODY, **over})


# ---------------------------------------------------------------- verify

def test_verify_happy_path_stores_encrypted_feed_and_returns_the_token_once(fake, scratch, caplog):
    caplog.set_level(logging.DEBUG)
    with TestClient(app) as c:
        r = verify(c)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["user_id"] == BODY["user_id"] and out["verified"] is True
    token = out["dashboard_token"]
    assert len(token) >= 40
    assert [req.url.host for req in fake.calls] == ["ns.example", "relay.test"]  # feed first, then the relay
    url_enc, tok_enc, verified_at, h = row(scratch, BODY["user_id"])
    assert h == sha(token) and verified_at is not None
    assert bytes(url_enc) != NS.encode() and b"ns-tok" not in bytes(tok_enc)
    assert accounts.decrypt(bytes(url_enc)) == NS and accounts.decrypt(bytes(tok_enc)) == "ns-tok"
    assert str(SGV) not in r.text
    assert all(str(SGV) not in rec.getMessage() for rec in caplog.records)


def test_reverify_replaces_the_dashboard_token_hash(fake, scratch):
    with TestClient(app) as c:
        t1 = verify(c).json()["dashboard_token"]
        t2 = verify(c).json()["dashboard_token"]
    assert t1 != t2
    assert row(scratch, BODY["user_id"])[3] == sha(t2)


@pytest.mark.parametrize("knob,value,detail", [
    ("ns_down", True, "feed unreachable"),
    ("ns_status", 500, "feed unreachable"),
    ("ns_body", {"status": "ok"}, "feed unreachable"),
    ("ns_status", 401, "feed refused the token"),
    ("ns_status", 403, "feed refused the token"),
    ("ns_age_ms", 16 * 60_000, "no reading in the last 15 minutes"),
    ("ns_body", [], "no reading in the last 15 minutes"),
    ("ns_body", [{"date": 1}], "no reading in the last 15 minutes"),
])
def test_feed_failures_are_422_with_the_pinned_detail(fake, knob, value, detail):
    setattr(fake, knob, value)
    with TestClient(app) as c:
        r = verify(c)
    assert r.status_code == 422 and r.json() == {"detail": detail}
    assert [req.url.host for req in fake.calls] == ["ns.example"]  # the relay is never told


@pytest.mark.parametrize("status", [401, 404])
def test_relay_refusal_is_404_no_such_account(fake, status):
    fake.relay_status = status
    with TestClient(app) as c:
        r = verify(c)
    assert r.status_code == 404 and r.json() == {"detail": "no such account"}


@pytest.mark.parametrize("knob,value", [("relay_down", True), ("relay_status", 500), ("relay_status", 503)])
def test_relay_unreachable_or_5xx_is_502(fake, knob, value):
    setattr(fake, knob, value)
    with TestClient(app) as c:
        assert verify(c).status_code == 502


@pytest.mark.parametrize("name", ["RELAY_KEY", "RELAY_CLOUD_KEY"])
def test_verify_is_503_while_a_key_is_unset(fake, monkeypatch, name):
    monkeypatch.setattr(accounts, name, "")
    with TestClient(app) as c:
        r = verify(c)
    assert r.status_code == 503
    assert fake.calls == []


@pytest.mark.parametrize("over", [
    {"nightscout_url": "ftp://ns.example"},
    {"nightscout_url": "https://ns.example/api?token=x"},
    {"nightscout_url": "https://" + "a" * 200},
    {"nightscout_url": "ns.example"},
    {"nightscout_token": ""},
    {"nightscout_token": "t" * 201},
    {"user_id": "../x"},
    {"user_bearer": ""},
])
def test_verify_body_validation(fake, over):
    with TestClient(app) as c:
        assert verify(c, **over).status_code == 422
    assert fake.calls == []


def test_url_with_a_path_and_trailing_slash_is_used_as_given(fake, monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(401)
    monkeypatch.setattr(accounts, "TRANSPORT", httpx.MockTransport(handler))
    with TestClient(app) as c:
        verify(c, nightscout_url="https://ns.example:8443/sub/")
    assert seen["url"] == "https://ns.example:8443/sub/api/v1/entries.json?count=1&token=ns-tok"


def test_verify_has_its_own_five_per_minute_bucket(fake, monkeypatch):
    fake.ns_status = 401
    clock = {"t": 1000.0}
    monkeypatch.setattr(main.time, "monotonic", lambda: clock["t"])
    h = {"X-Forwarded-For": "9.9.9.9, 10.0.0.1"}
    with TestClient(app) as c:
        assert [c.post("/v1/accounts/verify", json=BODY, headers=h).status_code for _ in range(5)] == [422] * 5
        assert c.post("/v1/accounts/verify", json=BODY, headers=h).status_code == 429
        assert c.post("/v1/accounts/verify", json=BODY, headers={"X-Forwarded-For": "8.8.8.8"}).status_code == 422
        clock["t"] += 61
        assert c.post("/v1/accounts/verify", json=BODY, headers=h).status_code == 422


# ---------------------------------------------------------------- dashboard credentials

@pytest.fixture
def drawn(monkeypatch):
    """dash.query replaced by a recorder: the resolver is under test, not the charts."""
    calls = []

    def fake_query(name, days, device_id, is_demo=False):
        calls.append((name, days, device_id, is_demo))
        return {"name": name, "device_id": device_id, "is_demo": is_demo, "days": days, "empty": True, "rows": []}
    monkeypatch.setattr(dash_mod, "query", fake_query)
    monkeypatch.setattr(main, "DEVICE_ID", "irin-test-0001")
    monkeypatch.setattr(main, "OWNER_BEARER", "owner-secret")
    return calls


def test_owner_bearer_draws_device_id(fake, drawn):
    with TestClient(app) as c:
        r = c.get("/v1/dash/nights", headers={"Authorization": "Bearer owner-secret"})
        assert r.status_code == 200 and r.json()["device_id"] == "irin-test-0001"
        r = c.get("/v1/dash/nights?demo=true", headers={"Authorization": "Bearer owner-secret"})
        assert r.json()["device_id"] == "irin-test-0001-demo" and r.json()["is_demo"] is True
    assert fake.calls == []


def test_phone_dashboard_token_draws_ns_user(fake, drawn, scratch):
    with TestClient(app) as c:
        token = verify(c).json()["dashboard_token"]
        r = c.get("/v1/dash/tir?days=7", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json()["device_id"] == f"ns-{BODY['user_id']}" and r.json()["empty"] is True
        assert drawn[-1] == ("tir", 7, f"ns-{BODY['user_id']}", False)
        r = c.get("/v1/dash/tir?demo=true", headers={"Authorization": f"Bearer {token}"})
        assert r.json()["device_id"] == f"ns-{BODY['user_id']}-demo"
        assert c.get("/v1/dash/tir", headers={"Authorization": "Bearer not-a-token"}).status_code == 401


def test_phone_token_works_without_owner_bearer(fake, drawn, scratch, monkeypatch):
    monkeypatch.setattr(main, "OWNER_BEARER", "")
    with TestClient(app) as c:
        token = verify(c).json()["dashboard_token"]
        assert c.get("/v1/dash/tir", headers={"Authorization": f"Bearer {token}"}).status_code == 200
        assert c.get("/v1/dash/tir", headers={"Authorization": "Bearer stranger"}).status_code == 503  # today's owner path


def test_old_phone_token_stops_working_after_reverify(fake, drawn, scratch):
    with TestClient(app) as c:
        t1 = verify(c).json()["dashboard_token"]
        t2 = verify(c).json()["dashboard_token"]
        assert c.get("/v1/dash/tir", headers={"Authorization": f"Bearer {t1}"}).status_code == 401
        assert c.get("/v1/dash/tir", headers={"Authorization": f"Bearer {t2}"}).status_code == 200


def test_owner_pairing_token_asks_the_relay_and_caches_both_answers(fake, drawn, monkeypatch):
    clock = {"t": 5000.0}
    monkeypatch.setattr(accounts.time, "monotonic", lambda: clock["t"])
    h = {"Authorization": "Bearer pair-tok", "X-Device-Id": "irin-test-0001"}
    with TestClient(app) as c:
        r = c.get("/v1/dash/nights", headers=h)
        assert r.status_code == 200 and r.json()["device_id"] == "irin-test-0001"
        assert c.get("/v1/dash/nights?demo=true", headers=h).json()["device_id"] == "irin-test-0001-demo"
        assert len(fake.calls) == 1  # the second read hit the cache
        assert json.loads(fake.calls[0].content) == {"device_id": "irin-test-0001", "token": "pair-tok"}
        clock["t"] += 301
        assert c.get("/v1/dash/nights", headers=h).status_code == 200
        assert len(fake.calls) == 2  # expired after 5 minutes
        fake.pair_ok = False
        bad = {"Authorization": "Bearer stale-tok", "X-Device-Id": "irin-test-0001"}
        assert c.get("/v1/dash/nights", headers=bad).status_code == 401
        assert c.get("/v1/dash/nights", headers=bad).status_code == 401
        assert len(fake.calls) == 3  # the refusal is cached too


def test_owner_pairing_token_for_another_device_is_404(fake, drawn):
    with TestClient(app) as c:
        r = c.get("/v1/dash/nights", headers={"Authorization": "Bearer pair-tok", "X-Device-Id": "irin-other"})
    assert r.status_code == 404


def test_owner_pairing_token_works_without_owner_bearer_and_relay_down_is_502(fake, drawn, monkeypatch):
    monkeypatch.setattr(main, "OWNER_BEARER", "")
    h = {"Authorization": "Bearer pair-tok", "X-Device-Id": "irin-test-0001"}
    with TestClient(app) as c:
        assert c.get("/v1/dash/nights", headers=h).status_code == 200
        fake.relay_down = True
        assert c.get("/v1/dash/nights", headers={**h, "Authorization": "Bearer other"}).status_code == 502


def test_no_credential_is_401(fake, drawn):
    with TestClient(app) as c:
        assert c.get("/v1/dash/nights").status_code == 401
        assert c.get("/v1/dash/nights", headers={"X-Device-Id": "irin-test-0001"}).status_code == 401


# ---------------------------------------------------------------- crypto

def test_secretbox_round_trip_under_sha256_of_relay_key(monkeypatch):
    monkeypatch.setattr(accounts, "RELAY_KEY", "relay-key")
    blob = accounts.encrypt("https://ns.example")
    assert accounts.decrypt(blob) == "https://ns.example"
    assert blob != accounts.encrypt("https://ns.example")  # a fresh nonce each time
    monkeypatch.setattr(accounts, "RELAY_KEY", "another")
    with pytest.raises(Exception):
        accounts.decrypt(blob)
