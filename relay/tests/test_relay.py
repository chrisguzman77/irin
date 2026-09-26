"""R6 check: key gating, bearer gating, single-use pairing, is_demo mismatch
rejected, /v0/log shows no plaintext, a card and a message round-trip
seal -> relay -> open; a document dump greps clean. Runs against the compose
mongo (or ATLAS_URI) in a throwaway database; skips without a server."""

import base64
import json
import os
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from nacl.public import Box, PrivateKey, PublicKey
from nacl.utils import random as nacl_random

os.environ.setdefault("ATLAS_URI", "mongodb://127.0.0.1:27017")
os.environ["RELAY_SOURCE_KEYS"] = "src-a,src-b"
os.environ["RELAY_DB_NAME"] = f"relay_test_{uuid.uuid4().hex[:8]}"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import store  # noqa: E402
from main import app  # noqa: E402

SRC = {"X-Source-Key": "src-a"}
TOKEN = "ab" * 16


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


def pair(c, token=TOKEN, is_demo=True, peer_kind="doctor"):
    """The whole handshake: device registers, browser completes, device confirms, browser collects the bearer."""
    device, doctor = PrivateKey.generate(), PrivateKey.generate()
    assert c.post("/v0/pair", json={"token": token, "device_pk": b64(bytes(device.public_key)), "is_demo": is_demo,
                                    "peer_kind": peer_kind, "device_id": "irin-test"}, headers=SRC).status_code == 200
    assert c.post(f"/v0/pair/{token}/complete", json={"doctor_pk": b64(bytes(doctor.public_key)),
                                                       "doctor_display_name": "Dr. Patel"}).status_code == 200
    r = c.post(f"/v0/pair/{token}/confirm", headers=SRC)
    assert r.status_code == 200 and r.json()["bearer_issued"] is True
    doctor_id = r.json()["doctor_id"]
    st = c.get(f"/v0/pair/{token}").json()
    bearer = st["bearer"]
    assert "bearer" not in c.get(f"/v0/pair/{token}").json()  # handed over exactly once
    return device, doctor, doctor_id, {"Authorization": f"Bearer {bearer}"}


def test_key_gating(c):
    body = {"token": TOKEN, "device_pk": b64(b"x" * 32), "is_demo": True}
    assert c.post("/v0/pair", json=body).status_code == 401
    assert c.post("/v0/pair", json=body, headers={"X-Source-Key": "nope"}).status_code == 401
    assert c.post("/v0/pair", json=body, headers={"X-Source-Key": "src-b"}).status_code == 200
    assert c.get("/v0/device/irin-test/messages").status_code == 401
    assert c.post("/v0/cards", json={}, headers={"X-Source-Key": "bad"}).status_code == 401


def test_pairing_is_single_use_and_expires(c):
    device, doctor, doctor_id, bearer = pair(c)
    assert c.post(f"/v0/pair/{TOKEN}/complete", json={"doctor_pk": b64(b"y" * 32), "doctor_display_name": "X"}).status_code == 409
    assert c.post(f"/v0/pair/{TOKEN}/confirm", headers=SRC).status_code == 409  # already confirmed
    assert c.post("/v0/pair", json={"token": TOKEN, "device_pk": b64(b"x" * 32)}, headers=SRC).status_code == 409
    # confirm before the browser completed
    t2 = "cd" * 16
    c.post("/v0/pair", json={"token": t2, "device_pk": b64(b"x" * 32)}, headers=SRC)
    assert c.post(f"/v0/pair/{t2}/confirm", headers=SRC).status_code == 409
    # expiry: a token older than 10 minutes is gone for both sides
    from datetime import timedelta

    store.db()["pairings"].update_one({"token": t2}, {"$set": {"expires_at": store.now() - timedelta(minutes=1)}})
    assert c.get(f"/v0/pair/{t2}").status_code == 404
    assert c.post(f"/v0/pair/{t2}/complete", json={"doctor_pk": b64(b"y" * 32), "doctor_display_name": "X"}).status_code == 404
    assert c.get("/v0/pair/unknown").status_code == 404


def test_card_round_trip_and_bearer_gating(c):
    device, doctor, doctor_id, bearer = pair(c)
    plaintext = json.dumps({"card_id": "standing:basal_check:2020-01-01:2020-01-14", "kind": "basal_check", "n": 8})
    nonce = nacl_random(Box.NONCE_SIZE)
    ct = Box(device, doctor.public_key).encrypt(plaintext.encode(), nonce).ciphertext
    env = {"recipient_id": doctor_id, "sender_id": "irin-test", "nonce": b64(nonce), "ciphertext": b64(ct),
           "source": "irin_bedside", "kind": "basal_check", "program": "standing", "is_demo": True,
           "card_id": "standing:basal_check:2020-01-01:2020-01-14"}
    assert c.post("/v0/cards", json=env, headers=SRC).status_code == 200
    assert c.post("/v0/cards", json=env, headers=SRC).status_code == 200  # a re-evaluation upserts
    assert c.get(f"/v0/inbox/{doctor_id}").status_code == 401  # no bearer
    assert c.get(f"/v0/inbox/{doctor_id}", headers={"Authorization": "Bearer nope"}).status_code == 401
    inbox = c.get(f"/v0/inbox/{doctor_id}", headers=bearer).json()
    assert len(inbox) == 1 and "plaintext" not in json.dumps(inbox) and inbox[0]["kind"] == "basal_check"
    opened = Box(doctor, device.public_key).decrypt(base64.b64decode(inbox[0]["ciphertext"]), base64.b64decode(inbox[0]["nonce"]))
    assert json.loads(opened)["n"] == 8
    # another doctor's bearer cannot read this inbox
    _, _, other_id, other = pair(c, token="ef" * 16)
    assert c.get(f"/v0/inbox/{doctor_id}", headers=other).status_code == 403


def test_is_demo_mismatch_is_rejected(c):
    device, doctor, doctor_id, bearer = pair(c, is_demo=True)
    env = {"recipient_id": doctor_id, "sender_id": "irin-test", "nonce": b64(b"n" * 24), "ciphertext": b64(b"c" * 40),
           "source": "irin_bedside", "kind": "basal_check", "program": "standing", "is_demo": False}
    assert c.post("/v0/cards", json=env, headers=SRC).status_code == 409  # a real card to a demo pairing
    assert c.post("/v0/cards", json={**env, "recipient_id": "nobody", "is_demo": True}, headers=SRC).status_code == 404
    msg = {"device_id": "irin-test", "message_id": "m1", "nonce": b64(b"n" * 24), "ciphertext": b64(b"c" * 40),
           "kind": "hold_step", "is_demo": False}
    assert c.post("/v0/messages", json=msg, headers=bearer).status_code == 409


def test_doctor_message_poll_and_resolution(c):
    device, doctor, doctor_id, bearer = pair(c)
    nonce = nacl_random(Box.NONCE_SIZE)
    ct = Box(doctor, device.public_key).encrypt(b'{"kind":"hold_step","hold_weeks":2}', nonce).ciphertext
    msg = {"device_id": "irin-test", "message_id": "m1", "nonce": b64(nonce), "ciphertext": b64(ct), "kind": "hold_step",
           "is_demo": True}
    assert c.post("/v0/messages", json=msg).status_code == 401
    assert c.post("/v0/messages", json=msg, headers=bearer).status_code == 200
    assert c.post("/v0/messages", json=msg, headers=bearer).status_code == 409  # message ids are single use
    poll = c.get("/v0/device/irin-test/messages", headers=SRC).json()
    assert [m["message_id"] for m in poll["messages"]] == ["m1"] and poll["messages"][0]["sender_id"] == doctor_id
    assert poll["pairings"] == [{"doctor_id": doctor_id, "status": "confirmed", "peer_kind": "doctor"}]
    opened = Box(device, doctor.public_key).decrypt(base64.b64decode(poll["messages"][0]["ciphertext"]), base64.b64decode(poll["messages"][0]["nonce"]))
    assert b"hold_step" in opened
    assert c.post("/v0/messages/m1/resolution", json={"status": "confirmed"}, headers=SRC).status_code == 200
    assert c.get("/v0/device/irin-test/messages", headers=SRC).json()["messages"] == []
    st = c.get("/v0/messages/m1", headers=bearer).json()
    assert st["status"] == "confirmed" and st["resolved_at"]
    assert c.post("/v0/messages/nope/resolution", json={"status": "declined"}, headers=SRC).status_code == 404


def test_revoke_from_either_side_deletes_keys(c):
    device, doctor, doctor_id, bearer = pair(c)
    _, _, other_id, other = pair(c, token="ef" * 16)
    assert c.post(f"/v0/pair/{doctor_id}/revoke", headers=other).status_code == 403  # not yours
    assert c.post(f"/v0/pair/{doctor_id}/revoke", headers=bearer).json()["status"] == "revoked"
    doc = store.db()["pairings"].find_one({"doctor_id": doctor_id})
    assert doc["status"] == "revoked" and "doctor_pk" not in doc and "bearer_hash" not in doc
    assert c.get(f"/v0/inbox/{doctor_id}", headers=bearer).status_code == 401  # the bearer died with it
    env = {"recipient_id": doctor_id, "sender_id": "irin-test", "nonce": b64(b"n" * 24), "ciphertext": b64(b"c" * 40),
           "source": "irin_bedside", "kind": "basal_check", "program": "standing", "is_demo": True}
    assert c.post("/v0/cards", json=env, headers=SRC).status_code == 404  # sends stop
    assert c.post(f"/v0/pair/{other_id}/revoke", headers=SRC).json()["status"] == "revoked"  # the device side
    assert c.get("/v0/device/irin-test/messages", headers=SRC).json()["pairings"][0]["status"] == "revoked"


def test_log_and_the_whole_store_hold_no_plaintext(c):
    device, doctor, doctor_id, bearer = pair(c)
    secret = json.dumps({"glucose_mgdl": 52, "low_point": "3:10 AM", "patient": "Chris"})
    nonce = nacl_random(Box.NONCE_SIZE)
    ct = Box(device, doctor.public_key).encrypt(secret.encode(), nonce).ciphertext
    c.post("/v0/cards", json={"recipient_id": doctor_id, "sender_id": "irin-test", "nonce": b64(nonce), "ciphertext": b64(ct),
                              "source": "irin_bedside", "kind": "hypo_response", "program": "standing", "is_demo": True},
           headers=SRC)
    log = c.get("/v0/log").json()
    assert log and all(set(e) <= {"at", "route", "sender", "recipient", "kind", "program", "is_demo", "size", "ciphertext_prefix",
                                   "card_id", "token_prefix", "peer_kind", "doctor_id", "by", "device", "message_id", "status"}
                       for e in log)
    dump = json.dumps([[store.public(d) for d in store.db()[name].find()] for name in store.COLLECTIONS], default=str)
    for word in ("mgdl", "glucose", "52", "3:10", "Chris", "low_point"):
        assert word not in dump, word
    assert "bearer_pending" not in dump  # delivered once, then gone
