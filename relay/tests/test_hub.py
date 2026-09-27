"""B3 check: the hub. Lease conflict (409 with the holder's expiry), reopen on
lease expiry, treating clears then returns at TOP urgency, the script only
to the live claim-holder, forbidden fields rejected, demo never in the real
hub, a call reaches the device poll, every claim audited, a buddy alert
rides /v0/cards to the watcher's inbox. Time moves by patching store.now,
never by sleeping. Runs against the compose mongo in a throwaway database."""

import base64
import json
import os
import sys
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from nacl.public import PrivateKey
from nacl.secret import SecretBox
from nacl.utils import random as nacl_random

os.environ.setdefault("ATLAS_URI", "mongodb://127.0.0.1:27017")
os.environ["RELAY_SOURCE_KEYS"] = "src-a,src-b"
os.environ["RELAY_DB_NAME"] = f"relay_test_{uuid.uuid4().hex[:8]}"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hub  # noqa: E402
import store  # noqa: E402
from main import app  # noqa: E402

SRC = {"X-Source-Key": "src-a"}
STEPS = ["Call Chris by name, loudly.", "If no answer, use the glucagon in the kitchen drawer."]


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


@pytest.fixture(scope="module", autouse=True)
def mongo():
    if store.status() != "ok":
        pytest.skip("no mongo at ATLAS_URI (start deploy/docker-compose.yml's mongo)")
    yield
    store.client().drop_database(store.DB_NAME)


@pytest.fixture
def c():
    store.client().drop_database(store.DB_NAME)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def clock(monkeypatch):
    """Move the relay's clock forward without sleeping."""
    base = store.now()
    state = {"t": base}
    monkeypatch.setattr(store, "now", lambda: state["t"])

    def advance(seconds):
        state["t"] = state["t"] + timedelta(seconds=seconds)

    return advance


def pair(c, token, is_demo=True, peer_kind="buddy"):
    """The whole handshake (test_relay.py's helper), a buddy by default."""
    device, peer = PrivateKey.generate(), PrivateKey.generate()
    assert c.post("/v0/pair", json={"token": token, "device_pk": b64(bytes(device.public_key)), "is_demo": is_demo,
                                    "peer_kind": peer_kind, "device_id": "irin-test"}, headers=SRC).status_code == 200
    assert c.post(f"/v0/pair/{token}/complete", json={"doctor_pk": b64(bytes(peer.public_key)),
                                                       "doctor_display_name": "Sam"}).status_code == 200
    r = c.post(f"/v0/pair/{token}/confirm", headers=SRC)
    assert r.status_code == 200
    bearer = c.get(f"/v0/pair/{token}").json()["bearer"]
    return r.json()["doctor_id"], {"Authorization": f"Bearer {bearer}"}


def sealed_script(steps=STEPS) -> dict:
    nonce = nacl_random(SecretBox.NONCE_SIZE)
    ct = SecretBox(hub.SCRIPT_KEY).encrypt(json.dumps({"steps": steps}).encode(), nonce).ciphertext
    return {"script_ciphertext": b64(ct), "script_nonce": b64(nonce)}


def listing(c, listing_id="L1", urgency=5, confidence="device_confirmed", is_demo=True, script=True, headers=SRC, **kw):
    body = {"listing_id": listing_id, "first_name": "Chris", "confidence": confidence, "elapsed_min": 10,
            "urgency": urgency, "is_demo": is_demo, "event_id": f"ev-{listing_id}", **kw}
    if script:
        body |= sealed_script()
    return c.post("/v0/hub/listing", json=body, headers=headers)


def test_listing_shape_and_upsert(c):
    r = listing(c)
    assert r.status_code == 200
    assert r.json() == {"listing_id": "L1", "first_name": "Chris", "status": "open", "confidence": "device_confirmed",
                        "elapsed_min": 10, "urgency": 5, "claim_expires_at": None, "treating_expires_at": None,
                        "is_demo": True}
    r = c.post("/v0/hub/listing", json={  # the device re-posts with a new elapsed_min, no script this time
        "listing_id": "L1", "first_name": "Chris", "confidence": "device_confirmed", "elapsed_min": 14, "urgency": 6,
        "is_demo": True, "event_id": "ev-L1"}, headers=SRC)
    assert r.status_code == 200 and r.json()["elapsed_min"] == 14 and r.json()["urgency"] == 6
    assert store.db()["hub_listings"].count_documents({}) == 1
    assert listing(c, headers={"X-Source-Key": "src-b"}).status_code == 404  # another device's listing
    assert listing(c, headers={}).status_code == 401


@pytest.mark.parametrize("field", ["glucose", "mgdl", "glucose_mgdl", "location", "lat", "lon", "phone", "email",
                                   "phone_number", "status", "anything_else"])
def test_forbidden_and_extra_fields_rejected(c, field):
    body = {"listing_id": "L1", "first_name": "Chris", "confidence": "unconfirmed", "elapsed_min": 10, "urgency": 5,
            "is_demo": True, "event_id": "ev", field: 52}
    assert c.post("/v0/hub/listing", json=body, headers=SRC).status_code == 422
    assert store.db()["hub_listings"].count_documents({}) == 0


def test_first_name_cannot_smuggle_a_number_and_scripts_must_open(c):
    assert listing(c, first_name="Chris 555-0100").status_code == 422
    assert listing(c, first_name="chris@example.com").status_code == 422
    bad = {"script_ciphertext": b64(b"x" * 40), "script_nonce": b64(b"n" * 24)}
    assert listing(c, script=False, **bad).status_code == 422
    assert listing(c, script=False, script_nonce=b64(b"n" * 24)).status_code == 422  # half a script


def test_list_order_and_volunteer_gating(c):
    _, vol = pair(c, "a1" * 16)
    _, doctor = pair(c, "a2" * 16, peer_kind="doctor")
    listing(c, "low", urgency=1)
    listing(c, "unconf", urgency=5, confidence="unconfirmed")
    listing(c, "conf", urgency=5)
    assert c.get("/v0/hub/list").status_code == 401
    assert c.get("/v0/hub/list", headers=doctor).status_code == 403  # a doctor pairing is not a volunteer
    assert [d["listing_id"] for d in c.get("/v0/hub/list", headers=vol).json()] == ["conf", "unconf", "low"]
    c.post("/v0/hub/resolve", json={"listing_id": "low", "outcome": "recovered"}, headers=SRC)
    assert [d["listing_id"] for d in c.get("/v0/hub/list", headers=vol).json()] == ["conf", "unconf"]


def test_demo_never_lands_in_the_real_hub(c):
    _, real = pair(c, "b1" * 16, is_demo=False)
    _, demo = pair(c, "b2" * 16, is_demo=True)
    listing(c, "D", is_demo=True)
    listing(c, "R", is_demo=False)
    assert [d["listing_id"] for d in c.get("/v0/hub/list", headers=real).json()] == ["R"]
    assert [d["listing_id"] for d in c.get("/v0/hub/list", headers=demo).json()] == ["D"]
    assert c.post("/v0/hub/claim", json={"listing_id": "D"}, headers=real).status_code == 404
    assert c.post("/v0/hub/claim", json={"listing_id": "R"}, headers=demo).status_code == 404
    assert listing(c, "D", is_demo=False).status_code == 409  # a listing never changes pool


def test_lease_conflict_409_with_the_holders_expiry(c):
    v1, h1 = pair(c, "c1" * 16)
    _, h2 = pair(c, "c2" * 16)
    listing(c)
    r = c.post("/v0/hub/claim", json={"listing_id": "L1"}, headers=h1)
    assert r.status_code == 200
    cl = r.json()
    assert set(cl) == {"claim_id", "listing_id", "volunteer_id", "claimed_at", "expires_at", "actions", "outcome"}
    assert cl["volunteer_id"] == v1 and cl["actions"] == ["claim"] and cl["outcome"] is None
    assert cl["expires_at"].endswith("+00:00")
    r2 = c.post("/v0/hub/claim", json={"listing_id": "L1"}, headers=h2)
    assert r2.status_code == 409
    assert r2.json()["holder_expires_at"] == cl["expires_at"] and r2.json()["detail"]
    assert c.get("/v0/hub/list", headers=h2).json()[0]["status"] == "claimed"


def test_expired_lease_reopens_the_listing(c, clock):
    _, h1 = pair(c, "d1" * 16)
    _, h2 = pair(c, "d2" * 16)
    listing(c)
    first = c.post("/v0/hub/claim", json={"listing_id": "L1"}, headers=h1).json()
    clock(hub.HUB_LEASE_S + 1)
    row = c.get("/v0/hub/list", headers=h2).json()[0]
    assert row["status"] == "open" and row["claim_expires_at"] is None
    assert c.post("/v0/hub/claim", json={"listing_id": "L1"}, headers=h2).status_code == 200
    audit = {a["claim_id"]: a for a in c.get("/v0/hub/audit", headers=SRC).json()}
    assert audit[first["claim_id"]]["outcome"] == "lease_expired"


def test_script_only_to_the_live_claim_holder(c, clock):
    _, h1 = pair(c, "e1" * 16)
    _, h2 = pair(c, "e2" * 16)
    listing(c)
    assert c.get("/v0/hub/claim/nope/script", headers=h1).status_code == 404  # before any claim
    cid = c.post("/v0/hub/claim", json={"listing_id": "L1"}, headers=h1).json()["claim_id"]
    assert c.get(f"/v0/hub/claim/{cid}/script", headers=h2).status_code == 403  # another volunteer
    assert c.get(f"/v0/hub/claim/{cid}/script").status_code == 401
    assert c.get(f"/v0/hub/claim/{cid}/script", headers=h1).json() == {"steps": STEPS}
    clock(hub.HUB_LEASE_S + 1)
    assert c.get(f"/v0/hub/claim/{cid}/script", headers=h1).status_code == 410  # the lease is over
    assert c.post("/v0/hub/call", json={"claim_id": cid}, headers=h1).status_code == 410
    # the script is stored sealed: no step text anywhere in the store
    dump = json.dumps([[store.public(d) for d in store.db()[n].find()] for n in store.COLLECTIONS], default=str)
    assert "glucagon" not in dump and "Call Chris" not in dump


def test_treating_clears_then_returns_at_top_urgency(c, clock):
    _, vol = pair(c, "f1" * 16)
    listing(c, "A", urgency=3)
    listing(c, "B", urgency=9)
    r = c.post("/v0/hub/treating", json={"listing_id": "A"}, headers=SRC)
    assert r.status_code == 200 and r.json()["status"] == "treating"
    assert r.json()["treating_expires_at"].endswith("+00:00")
    assert c.post("/v0/hub/claim", json={"listing_id": "A"}, headers=vol).status_code == 409  # cleared: not claimable
    clock(hub.HUB_TREATING_S - 5)
    assert {d["listing_id"]: d["status"] for d in c.get("/v0/hub/list", headers=vol).json()}["A"] == "treating"
    clock(10)
    rows = c.get("/v0/hub/list", headers=vol).json()
    assert rows[0]["listing_id"] == "A" and rows[0]["status"] == "open" and rows[0]["urgency"] == 10
    assert rows[0]["treating_expires_at"] is None
    # the device's next re-post does not knock it off the top
    assert listing(c, "A", urgency=3).json()["urgency"] == 10


def test_resolve_closes_the_live_claim_with_the_outcome(c):
    _, vol = pair(c, "0a" * 16)
    listing(c)
    cid = c.post("/v0/hub/claim", json={"listing_id": "L1"}, headers=vol).json()["claim_id"]
    r = c.post("/v0/hub/resolve", json={"listing_id": "L1", "outcome": "acknowledged"}, headers=SRC)
    assert r.status_code == 200 and r.json()["status"] == "resolved"
    assert c.get(f"/v0/hub/claim/{cid}/script", headers=vol).status_code == 410
    assert c.get("/v0/hub/audit", headers=SRC).json()[0]["outcome"] == "acknowledged"
    assert c.post("/v0/hub/resolve", json={"listing_id": "L1", "outcome": "x"}, headers={"X-Source-Key": "src-b"}).status_code == 404
    assert c.post("/v0/hub/treating", json={"listing_id": "L1"}, headers=SRC).status_code == 409


def test_call_reaches_the_device_poll_once(c):
    _, vol = pair(c, "0b" * 16)
    listing(c)
    cid = c.post("/v0/hub/claim", json={"listing_id": "L1"}, headers=vol).json()["claim_id"]
    assert c.post("/v0/hub/call", json={"claim_id": cid}, headers=vol).json() == {"status": "ringing"}
    poll = c.get("/v0/device/irin-test/messages", headers=SRC).json()
    assert set(poll) == {"messages", "pairings", "calls", "matches"}  # B3+ added matches
    assert [(x["listing_id"], x["claim_id"]) for x in poll["calls"]] == [("L1", cid)]
    assert poll["calls"][0]["at"].endswith("+00:00")
    assert c.get("/v0/device/irin-test/messages", headers=SRC).json()["calls"] == []  # delivered once
    assert c.get("/v0/device/irin-test/messages", headers={"X-Source-Key": "src-b"}).json()["calls"] == []


def test_audit_lists_every_claim_and_the_log_holds_ids_only(c, clock):
    v1, h1 = pair(c, "0c" * 16)
    v2, h2 = pair(c, "0d" * 16)
    listing(c)
    c1 = c.post("/v0/hub/claim", json={"listing_id": "L1"}, headers=h1).json()["claim_id"]
    c.get(f"/v0/hub/claim/{c1}/script", headers=h1)
    c.post("/v0/hub/call", json={"claim_id": c1}, headers=h1)
    clock(hub.HUB_LEASE_S + 1)
    c2 = c.post("/v0/hub/claim", json={"listing_id": "L1"}, headers=h2).json()["claim_id"]
    c.post("/v0/hub/resolve", json={"listing_id": "L1", "outcome": "recovered"}, headers=SRC)
    assert c.get("/v0/hub/audit").status_code == 401
    audit = c.get("/v0/hub/audit", headers=SRC).json()
    assert [(a["claim_id"], a["volunteer_id"], a["actions"], a["outcome"]) for a in audit] == [
        (c1, v1, ["claim", "script", "call"], "lease_expired"), (c2, v2, ["claim"], "recovered")]
    assert all(a["claimed_at"].endswith("+00:00") for a in audit)
    assert c.get("/v0/hub/audit", headers={"X-Source-Key": "src-b"}).json() == []
    hub_log = [e for e in c.get("/v0/log").json() if e["route"].startswith("hub.")]
    assert {e["route"] for e in hub_log} >= {"hub.listing", "hub.claim", "hub.script", "hub.call", "hub.resolve",
                                             "hub.lease_expired"}
    assert "Chris" not in json.dumps(hub_log)  # ids and kinds only, never the first name


def test_buddy_alert_rides_cards_to_the_watchers_inbox(c):
    buddy_id, watcher = pair(c, "1a" * 16)
    doctor_id, _ = pair(c, "1b" * 16, peer_kind="doctor")
    env = {"recipient_id": buddy_id, "sender_id": "irin-test", "nonce": b64(b"n" * 24), "ciphertext": b64(b"c" * 40),
           "source": "irin_bedside", "kind": "buddy_alert", "is_demo": True, "card_id": "alert-1"}
    assert c.post("/v0/cards", json=env, headers=SRC).status_code == 200
    assert c.post("/v0/cards", json={**env, "program": "buddy", "card_id": "alert-2"}, headers=SRC).status_code == 200
    assert c.post("/v0/cards", json={**env, "program": "standing"}, headers=SRC).status_code == 422
    assert c.post("/v0/cards", json={**env, "recipient_id": doctor_id}, headers=SRC).status_code == 409
    assert c.post("/v0/cards", json={**env, "kind": "basal_check", "program": "standing"}, headers=SRC).status_code == 409
    inbox = c.get(f"/v0/inbox/{buddy_id}", headers=watcher).json()
    assert [(m["kind"], m["card_id"]) for m in inbox] == [("buddy_alert", "alert-1"), ("buddy_alert", "alert-2")]
    assert inbox[0]["created_at"].endswith("+00:00")


def test_a_plain_script_from_the_device_is_sealed_at_rest_and_opens_for_the_holder(c, clock):
    """The device sends {steps} in the clear over the key-gated route; the relay seals it."""
    _, h1 = pair(c, "f1" * 16)
    body = {"listing_id": "LP", "first_name": "Sam", "confidence": "device_confirmed", "elapsed_min": 12, "urgency": 5,
            "is_demo": True, "event_id": "ev-p", "script": {"steps": STEPS}}
    assert c.post("/v0/hub/listing", json=body, headers=SRC).status_code == 200
    dump = json.dumps([store.public(d) for d in store.db()["hub_listings"].find()], default=str)
    assert all(step not in dump for step in STEPS)
    cid = c.post("/v0/hub/claim", json={"listing_id": "LP"}, headers=h1).json()["claim_id"]
    assert c.get(f"/v0/hub/claim/{cid}/script", headers=h1).json() == {"steps": STEPS}
    bad = {**body, "listing_id": "LQ", "script": {"steps": STEPS, "phone": "555"}}
    assert c.post("/v0/hub/listing", json=bad, headers=SRC).status_code == 422


def test_buddy_line_rides_cards_to_the_buddy_only(c):
    buddy_id, watcher = pair(c, "1c" * 16)
    doctor_id, _ = pair(c, "1d" * 16, peer_kind="doctor")
    env = {"recipient_id": buddy_id, "sender_id": "irin-test", "nonce": b64(b"n" * 24), "ciphertext": b64(b"c" * 40),
           "source": "irin_bedside", "kind": "buddy_line", "program": "buddy", "is_demo": True,
           "card_id": "bl-2020-01-01"}
    assert c.post("/v0/cards", json=env, headers=SRC).status_code == 200
    assert c.post("/v0/cards", json={**env, "ciphertext": b64(b"d" * 40)}, headers=SRC).status_code == 200  # replaced
    assert c.post("/v0/cards", json={**env, "recipient_id": doctor_id}, headers=SRC).status_code == 409
    assert c.post("/v0/cards", json={**env, "recipient_id": doctor_id, "program": None}, headers=SRC).status_code == 409
    inbox = c.get(f"/v0/inbox/{buddy_id}", headers=watcher).json()
    assert [(m["kind"], m["card_id"], m["ciphertext"]) for m in inbox] == [("buddy_line", "bl-2020-01-01", b64(b"d" * 40))]
