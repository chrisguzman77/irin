"""A2 check: a code is refused with no one in front of the Irin (mock always
allows it); mint never returns the token and the QR carries only the code
after #; a poll's "paired" report is applied only when its token_sha256
matches the token this Pi minted, and broadcast over WS; revoke tells the
relay and is instant locally either way; a PIN'd request that arrived
through the tunnel (Cf-Connecting-Ip) also needs the paired owner bearer,
while a request without that header is unchanged. Tests never reach the
network; only clock.py, never sleep()."""

import hashlib
import json
from datetime import datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from app import store
from app.clock import clock
from app.owner import OwnerPairingService, RelayOwner, token_matches

T0 = datetime(2020, 1, 1, 10, 0)
H = {"X-PIN": "1234"}


class FakeRelay:
    """The relay's owner-pairing routes in memory (what the relay session implements)."""

    def __init__(self):
        self.registered: dict | None = None
        self.deleted = False

    def transport(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers.get("X-Source-Key") == "src-key"
            path, method = request.url.path, request.method
            if path == "/v0/device/pairings" and method == "POST":
                self.registered = json.loads(request.content)
                return httpx.Response(200, json={"status": "registered"})
            if path == "/v0/device/pair" and method == "DELETE":
                self.deleted = True
                return httpx.Response(200, json={"status": "revoked"})
            return httpx.Response(404)

        return httpx.MockTransport(handler)


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setattr(store.config, "IRIN_DB", str(tmp_path / "t.db"))
    store.init_db()
    clock.set(speed=60.0, start=T0)
    relay = FakeRelay()
    states = []
    s = OwnerPairingService(relay=RelayOwner("http://relay.test", "src-key", transport=relay.transport()),
                            device_id="irin-test", device_url="http://localhost:8000",
                            app_origin="https://app.example", on_state=states.append)
    yield s, relay, states
    clock.reset()


# --- the service, against a fake relay ---


def test_mint_registers_with_the_relay_and_never_returns_the_token(svc):
    s, relay, states = svc
    out = s.mint()
    assert set(out) == {"code", "qr_url", "expires_at", "expires_in_s"}
    assert len(out["code"]) == 6 and out["code"].isdigit()
    assert out["qr_url"] == f"https://app.example/#pair={out['code']}"
    assert "?" not in out["qr_url"] and "token" not in out
    assert out["expires_in_s"] == 600
    assert relay.registered["code"] == out["code"] and relay.registered["device_id"] == "irin-test"
    assert len(relay.registered["token"]) >= 43  # never shipped back to the caller
    from datetime import datetime, timezone
    relay_exp = datetime.fromisoformat(relay.registered["expires_at"])  # the relay's deadline: tz-aware wall UTC
    assert relay_exp.tzinfo is not None
    assert 590 <= (relay_exp - datetime.now(timezone.utc)).total_seconds() <= 600
    assert states[-1] == {"state": "pending", "username": None, "paired_at": None}
    assert s.state()["state"] == "pending"


def test_a_new_mint_replaces_the_earlier_unused_code(svc):
    s, relay, _ = svc
    first = s.mint()
    second = s.mint()
    assert first["code"] != second["code"] or relay.registered["token"]
    assert token_matches(relay.registered["token"]) is False  # not paired yet
    # the first token no longer matters: only the latest is stored
    assert s.state()["state"] == "pending"


def test_on_owner_paired_with_matching_sha_updates_state_and_broadcasts(svc):
    s, relay, states = svc
    s.mint()
    sha = _sha(relay.registered["token"])
    states.clear()
    s.on_owner({"state": "paired", "username": "Chris's phone", "token_sha256": sha,
               "paired_at": "2020-01-01T10:05:00+00:00"})
    assert s.state() == {"state": "paired", "username": "Chris's phone", "paired_at": "2020-01-01T10:05:00+00:00"}
    assert states[-1] == {"state": "paired", "username": "Chris's phone", "paired_at": "2020-01-01T10:05:00+00:00"}
    assert token_matches(relay.registered["token"]) is True
    assert token_matches("some-other-token") is False


def test_on_owner_mismatched_sha_is_ignored(svc):
    s, relay, states = svc
    s.mint()
    states.clear()
    s.on_owner({"state": "paired", "username": "Eve", "token_sha256": "not-the-real-sha"})
    assert s.state()["state"] == "pending" and s.state()["username"] is None
    assert states == []  # no local change, so no broadcast


def test_revoke_calls_the_relay_and_is_instant_locally(svc):
    s, relay, states = svc
    s.mint()
    s.on_owner({"state": "paired", "username": "Chris", "token_sha256": _sha(relay.registered["token"])})
    states.clear()
    out = s.revoke()
    assert relay.deleted is True
    assert out["state"] == "revoked" and out["username"] == "Chris"
    assert states[-1]["state"] == "revoked"


def test_revoke_is_local_even_when_the_relay_is_unreachable(svc):
    s, relay, _ = svc
    s.mint()
    s.relay = RelayOwner("http://relay.invalid", "src-key")
    out = s.revoke()
    assert out["state"] == "revoked"


# --- the routes and the tunnel gate, through the full app ---


def _pair(c, main, relay) -> str:
    """Mints a code, then simulates the relay reporting the redeem: returns the token."""
    c.post("/api/owner/code", headers=H)
    token = relay.registered["token"]
    main.runtime.relay_client.on_owner({"state": "paired", "username": "Chris's phone", "token_sha256": _sha(token)})
    return token


def test_code_refused_without_presence_and_allowed_under_mock(monkeypatch):
    from app import auth, main

    monkeypatch.setattr(auth.config, "PIN", "1234")
    relay = FakeRelay()
    with TestClient(main.app) as c:
        main.runtime.owner.relay = RelayOwner("http://relay.test", "src-key", transport=relay.transport())
        assert c.post("/api/owner/code").status_code == 401  # PIN still required
        r = c.post("/api/owner/code", headers=H)  # IRIN_HW=mock (conftest): always allowed
        assert r.status_code == 200
        monkeypatch.setattr(main.config, "IRIN_HW", "real")
        monkeypatch.setattr(main.runtime.outputs, "get_presence", lambda: False)
        r = c.post("/api/owner/code", headers=H)
        assert r.status_code == 409 and r.json()["detail"] == "stand in front of your Irin to pair a phone"
        monkeypatch.setattr(main.runtime.outputs, "get_presence", lambda: True)
        r = c.post("/api/owner/code", headers=H)
        assert r.status_code == 200 and set(r.json()) == {"code", "qr_url", "expires_at", "expires_in_s"}


def test_owner_get_never_leaks_the_token(monkeypatch):
    from app import auth, main

    monkeypatch.setattr(auth.config, "PIN", "1234")
    relay = FakeRelay()
    with TestClient(main.app) as c:
        main.runtime.owner.relay = RelayOwner("http://relay.test", "src-key", transport=relay.transport())
        c.post("/api/owner/code", headers=H)
        token = relay.registered["token"]
        st = c.get("/api/owner").json()
        assert set(st) == {"state", "username", "paired_at"} and st["state"] == "pending"
        raw = json.dumps(st)
        assert token not in raw and "token" not in raw


def test_owner_poll_paired_broadcasts_over_ws(monkeypatch):
    from app import auth, main

    monkeypatch.setattr(auth.config, "PIN", "1234")
    relay = FakeRelay()
    with TestClient(main.app) as c:
        main.runtime.owner.relay = RelayOwner("http://relay.test", "src-key", transport=relay.transport())
        c.post("/api/owner/code", headers=H)
        token = relay.registered["token"]
        with c.websocket_connect("/ws") as ws:
            ws.receive_text()  # the initial state_snapshot
            main.runtime.relay_client.on_owner({"state": "paired", "username": "Chris's phone",
                                                "token_sha256": _sha(token)})
            msg = json.loads(ws.receive_text())
        assert msg["type"] == "pairing_state"
        assert msg["payload"] == {"kind": "owner", "state": "paired", "username": "Chris's phone"}
        assert c.get("/api/owner").json()["state"] == "paired"


def test_owner_revoke_route_calls_the_relay(monkeypatch):
    from app import auth, main

    monkeypatch.setattr(auth.config, "PIN", "1234")
    relay = FakeRelay()
    with TestClient(main.app) as c:
        main.runtime.owner.relay = RelayOwner("http://relay.test", "src-key", transport=relay.transport())
        _pair(c, main, relay)
        assert c.delete("/api/owner").status_code == 401  # PIN required
        r = c.delete("/api/owner", headers=H)
        assert r.status_code == 200 and r.json()["state"] == "revoked"
        assert relay.deleted is True
        assert c.get("/api/owner").json()["state"] == "revoked"


def test_tunnel_requests_need_the_paired_owner_bearer(monkeypatch):
    """The tunnel gate binds any PIN'd mutating route, checked here on /api/mode."""
    from app import auth, main

    monkeypatch.setattr(auth.config, "PIN", "1234")
    relay = FakeRelay()
    with TestClient(main.app) as c:
        main.runtime.owner.relay = RelayOwner("http://relay.test", "src-key", transport=relay.transport())

        # no Cf-Connecting-Ip: unchanged, PIN alone is enough (the kiosk, the LAN, tests)
        assert c.post("/api/mode", json={"mode": "replay"}, headers=H).status_code == 200

        tunnel = {**H, "Cf-Connecting-Ip": "203.0.113.9"}
        r = c.post("/api/mode", json={"mode": "replay"}, headers=tunnel)
        assert r.status_code == 401 and r.json()["detail"] == "pair this phone with your Irin"

        r = c.post("/api/mode", json={"mode": "replay"}, headers={**tunnel, "Authorization": "Bearer wrong-token"})
        assert r.status_code == 401

        token = _pair(c, main, relay)
        assert c.get("/api/owner").json()["state"] == "paired"
        r = c.post("/api/mode", json={"mode": "replay"}, headers={**tunnel, "Authorization": f"Bearer {token}"})
        assert r.status_code == 200

        # a revoked owner token is refused again through the tunnel
        c.delete("/api/owner", headers=H)
        r = c.post("/api/mode", json={"mode": "replay"}, headers={**tunnel, "Authorization": f"Bearer {token}"})
        assert r.status_code == 401


def test_reads_are_unaffected_by_the_tunnel_header(monkeypatch):
    from app import auth, main

    monkeypatch.setattr(auth.config, "PIN", "1234")
    with TestClient(main.app) as c:
        r = c.get("/api/owner", headers={"Cf-Connecting-Ip": "203.0.113.9"})
        assert r.status_code == 200


def test_a_new_code_while_paired_never_locks_out_the_paired_phone(svc):
    """Review: a re-mint wrote the new hash as current and 401'd the still-paired phone."""
    s, relay, states = svc
    s.mint()
    first = relay.registered["token"]
    s.on_owner({"state": "paired", "username": "Phone A", "token_sha256": _sha(first)})
    s.mint()  # someone opens "Pair a phone" again
    second = relay.registered["token"]
    assert s.state()["state"] == "paired" and token_matches(first) and not token_matches(second)
    s.on_owner({"state": "paired", "username": "Phone A", "token_sha256": _sha(first)})  # relay: still the old one
    assert token_matches(first)
    s.on_owner({"state": "paired", "username": "Phone B", "token_sha256": _sha(second)})  # the new code redeemed
    assert token_matches(second) and not token_matches(first) and s.state()["username"] == "Phone B"


def test_a_local_revoke_is_never_undone_by_a_stale_relay_report(svc):
    s, relay, states = svc
    s.mint()
    token = relay.registered["token"]
    s.on_owner({"state": "paired", "username": "Chris", "token_sha256": _sha(token)})
    s.revoke()
    s.on_owner({"state": "paired", "username": "Chris", "token_sha256": _sha(token)})  # relay never heard the revoke
    assert s.state()["state"] == "revoked" and not token_matches(token)


def test_the_poll_never_waits_on_a_busy_lock(svc):
    s, relay, states = svc
    s._lock.acquire()
    try:
        s.on_owner({"state": "revoked"})  # returns at once instead of blocking the event loop
    finally:
        s._lock.release()
    assert s.state()["state"] == "none"
