"""R5 check: expired and reused tokens are rejected; not paired until the
device confirms with a fresh PIN; revoke stops sends (recipients() drops it)
and wipes the peer key; demo pairings are is_demo and only receive demo
cards; the QR URL keeps the token after #."""

import json
from datetime import datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app import store
from app.clock import clock
from app.rounds import crypto
from app.rounds.pairing import PairingError, PairingService, RelayPairing

T0 = datetime(2020, 1, 1, 10, 0)
H = {"X-PIN": "1234"}


class FakeRelay:
    """The relay's pairing routes in memory (what R6 implements)."""

    def __init__(self):
        self.tokens = {}
        self.revoked = []
        self.n = 0

    def transport(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers.get("X-Source-Key") == "src-key"
            path = request.url.path
            if path == "/v0/pair" and request.method == "POST":
                body = json.loads(request.content)
                assert body["device_id"]  # the relay routes messages and revokes by it
                self.tokens[body["token"]] = {"status": "pending", **body}
                return httpx.Response(200, json={"ok": True})
            if path.startswith("/v0/pair/") and path.endswith("/confirm"):
                tok = self.tokens.get(path.split("/")[3])
                if not tok or tok["status"] != "completed":
                    return httpx.Response(409, json={"detail": "not completed"})
                self.n += 1
                tok["status"] = "confirmed"
                return httpx.Response(200, json={"pairing_id": f"pair-{self.n}", "doctor_id": f"doc-{self.n}"})
            if path.startswith("/v0/pair/") and path.endswith("/revoke"):
                self.revoked.append(path.split("/")[3])
                return httpx.Response(200, json={"ok": True})
            if path.startswith("/v0/pair/") and request.method == "GET":
                tok = self.tokens.get(path.split("/")[3])
                return httpx.Response(200, json=tok) if tok else httpx.Response(404)
            return httpx.Response(404)

        return httpx.MockTransport(handler)

    def scan(self, token, doctor_pk, name="Dr. Patel"):
        """The browser side: POST /v0/pair/{token}/complete."""
        self.tokens[token].update(status="completed", doctor_pk=doctor_pk, doctor_display_name=name)


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setattr(store.config, "IRIN_DB", str(tmp_path / "t.db"))
    store.init_db()
    monkeypatch.setattr(crypto, "KEYS_DIR", tmp_path / "keys")
    clock.set(speed=60.0, start=T0)
    relay = FakeRelay()
    states = []
    s = PairingService(relay=RelayPairing("http://relay.test", "src-key", transport=relay.transport()),
                       device_id="irin-test", device_pk_fn=crypto.device_public_key, inbox_url="https://doctor.example",
                       watch_url="https://watch.example", relay_url="http://relay.test", is_demo=lambda: True,
                       on_state=states.append)
    yield s, relay, states
    clock.reset()


def test_handshake_pairs_only_after_the_device_confirms(svc):
    s, relay, states = svc
    out = s.start("doctor")
    assert out["qr_url"].startswith("https://doctor.example/pair#token=") and "device_pk=" in out["qr_url"]
    assert "?" not in out["qr_url"] and out["is_demo"] is True and len(out["token"]) == 32
    assert s.state()["status"] == "awaiting_scan" and s.recipients(is_demo=True) == []
    _, doc_pk = crypto.generate_keypair()
    relay.scan(out["token"], doc_pk)
    st = s.poll()
    assert st["status"] == "awaiting_confirm" and st["code4"] == crypto.code4(s.device_pk, doc_pk, out["token"])
    assert st["doctor_display_name"] == "Dr. Patel" and s.recipients(is_demo=True) == []  # still not paired
    p = s.confirm()
    assert p.status == "paired" and p.doctor_pk == doc_pk and p.is_demo is True and p.peer_kind == "doctor"
    assert s.state()["status"] == "idle" and [r.doctor_id for r in s.recipients(is_demo=True)] == [p.doctor_id]
    assert s.recipients(is_demo=False) == []  # a demo pairing never gets a real card
    assert store.select_pairings()[0].doctor_id == p.doctor_id and states[-1]["status"] == "idle"
    assert "doctor_pk" not in states[-1]["pairings"][0]  # the key never leaves the device through the state


def test_expired_token_is_rejected(svc):
    s, relay, _ = svc
    out = s.start()
    _, doc_pk = crypto.generate_keypair()
    relay.scan(out["token"], doc_pk)
    clock.advance(10 * 60 * 60 + 60)  # 10 wall minutes at 60x, and a little
    with pytest.raises(PairingError) as e:
        s.confirm()
    assert e.value.status == 410 and s.state()["status"] == "idle" and s.recipients(True) == []


def test_reused_token_and_confirm_before_scan_are_rejected(svc):
    s, relay, _ = svc
    with pytest.raises(PairingError) as e:
        s.confirm()
    assert e.value.status == 409
    out = s.start()
    with pytest.raises(PairingError) as e:
        s.confirm()  # nobody has scanned
    assert e.value.status == 409
    _, doc_pk = crypto.generate_keypair()
    relay.scan(out["token"], doc_pk)
    s.confirm()
    with pytest.raises(PairingError):
        s.confirm()  # single use
    assert len(s.pairings) == 1


def test_revoke_stops_sends_and_wipes_the_key(svc):
    s, relay, _ = svc
    out = s.start("buddy")
    assert out["qr_url"].startswith("https://watch.example/pair#")
    _, doc_pk = crypto.generate_keypair()
    relay.scan(out["token"], doc_pk, name="Sam")
    p = s.confirm()
    assert p.peer_kind == "buddy" and s.recipients(True)
    r = s.revoke(p.doctor_id)
    assert r.status == "revoked" and r.doctor_pk == "" and s.recipients(True) == []
    assert relay.revoked == [p.doctor_id] and store.select_pairings()[0].status == "revoked"
    with pytest.raises(PairingError):
        s.revoke("nope")


def test_pairings_survive_a_restart(svc):
    s, relay, _ = svc
    out = s.start()
    _, doc_pk = crypto.generate_keypair()
    relay.scan(out["token"], doc_pk)
    p = s.confirm()
    again = PairingService(relay=s.relay, device_id="irin-test", device_pk_fn=crypto.device_public_key,
                           inbox_url="x", watch_url="x", relay_url="x", is_demo=lambda: True)
    assert [r.doctor_id for r in again.recipients(True)] == [p.doctor_id]


def test_relay_down_is_a_502_never_a_crash(svc):
    s, relay, _ = svc
    s.relay = RelayPairing("http://relay.invalid", "src-key")
    with pytest.raises(PairingError) as e:
        s.start()
    assert e.value.status == 502


def test_endpoints_confirm_needs_the_fresh_pin_route(monkeypatch, tmp_path):
    from app import auth, main
    from app.contracts import FRESH_PIN_ENDPOINTS

    monkeypatch.setattr(auth.config, "PIN", "1234")
    relay = FakeRelay()
    with TestClient(main.app) as c:
        main.runtime.pairing.relay = RelayPairing("http://relay.test", "src-key", transport=relay.transport())
        assert "/api/pair/confirm" in FRESH_PIN_ENDPOINTS
        assert c.post("/api/pair/start", json={"peer_kind": "doctor"}).status_code == 401
        r = c.post("/api/pair/start", json={"peer_kind": "doctor"}, headers=H)
        assert r.status_code == 200 and r.json()["qr_url"].split("#")[0].endswith("/pair")
        assert c.get("/api/pair/status").status_code == 401  # it drives relay traffic: PIN
        assert c.get("/api/pair/status", headers=H).json()["status"] == "awaiting_scan"
        assert c.post("/api/pair/confirm", headers=H).status_code == 409  # not scanned yet
        _, doc_pk = crypto.generate_keypair()
        relay.scan(r.json()["token"], doc_pk)
        st = c.get("/api/pair/status", headers=H).json()
        assert st["status"] == "awaiting_confirm" and len(st["code4"]) == 4
        p = c.post("/api/pair/confirm", headers=H).json()
        assert p["status"] == "paired" and p["doctor_pk"] == "" and p["is_demo"] is True
        assert c.get("/api/pairings").json()[0]["doctor_id"] == p["doctor_id"]
        with c.websocket_connect("/ws") as ws:
            snap = json.loads(ws.receive_text())["payload"]
            assert snap["pairing_state"]["pairings"][0]["status"] == "paired"
        assert c.post(f"/api/pair/{p['doctor_id']}/revoke", headers=H).json()["status"] == "revoked"


def test_token_lives_ten_wall_minutes_at_replay_speed(svc):
    """At 60x a clock-time TTL would kill the QR in 10 wall seconds."""
    s, relay, _ = svc
    out = s.start()
    assert out["expires_in_s"] == 600
    clock.advance(10 * 60 * 60 - 60)  # 599 wall seconds at 60x
    _, doc_pk = crypto.generate_keypair()
    relay.scan(out["token"], doc_pk)
    assert s.confirm().status == "paired"


def test_malformed_relay_key_drops_the_pairing_and_never_breaks_state(svc):
    s, relay, _ = svc
    out = s.start()
    relay.tokens[out["token"]].update(status="completed", doctor_pk="not-base64!", doctor_display_name="X")
    with pytest.raises(PairingError) as e:
        s.poll()
    assert e.value.status == 502 and s.state()["status"] == "idle"
    out = s.start()
    relay.tokens[out["token"]].update(status="completed", doctor_pk=12345, doctor_display_name="X")
    with pytest.raises(PairingError):
        s.confirm()
    assert s.state()["code4"] is None and s.pairings == {}


def test_relay_confirm_without_an_id_pairs_nothing(svc):
    s, relay, _ = svc
    out = s.start()
    _, doc_pk = crypto.generate_keypair()
    relay.scan(out["token"], doc_pk)
    real = relay.transport

    def handler(request):
        if request.url.path.endswith("/confirm"):
            return httpx.Response(200, json={})
        return real().handler(request)

    s.relay = RelayPairing("http://relay.test", "src-key", transport=httpx.MockTransport(handler))
    with pytest.raises(PairingError) as e:
        s.confirm()
    assert e.value.status == 502 and s.pairings == {}


def test_mode_switch_cancels_a_pending_token(monkeypatch):
    from app import auth, main

    monkeypatch.setattr(auth.config, "PIN", "1234")
    relay = FakeRelay()
    with TestClient(main.app) as c:
        main.runtime.pairing.relay = RelayPairing("http://relay.test", "src-key", transport=relay.transport())
        assert c.post("/api/pair/start", json={}, headers=H).status_code == 200
        assert c.get("/api/pair/status", headers=H).json()["status"] == "awaiting_scan"
        assert c.post("/api/mode", json={"mode": "nightscout"}, headers=H).status_code == 200
        assert c.get("/api/pair/status", headers=H).json()["status"] == "idle"
        c.post("/api/mode", json={"mode": "replay"}, headers=H)
