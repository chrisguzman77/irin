"""A2/R5+ check: owner pairing (the phone app <-> this user's Pi). Register ->
redeem -> the device poll shows paired with token_sha256; an unknown, used,
or expired code is 404 with the same message; the plaintext token is dropped
at redeem; DELETE by the app's bearer or by the Pi's source key revokes and
is idempotent; a second redeem revokes the first token; another source key
cannot register for this device; the log never carries code, token, or
username. Runs against the compose mongo in a throwaway database; skips
without a server."""

import json
import os
import secrets
import sys
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("ATLAS_URI", "mongodb://127.0.0.1:27017")
os.environ["RELAY_SOURCE_KEYS"] = "src-a,src-b"
os.environ["RELAY_DB_NAME"] = f"relay_test_{uuid.uuid4().hex[:8]}"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import owner  # noqa: E402
import store  # noqa: E402
from main import app  # noqa: E402

SRC = {"X-Source-Key": "src-a"}
SRC_B = {"X-Source-Key": "src-b"}


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


def register(c, device_id, code, headers=SRC, minutes=10, device_url="http://localhost:8000", token=None) -> str:
    token = token or secrets.token_urlsafe(32)
    expires_at = (store.now() + timedelta(minutes=minutes)).isoformat()
    r = c.post("/v0/device/pairings", json={"code": code, "device_id": device_id, "device_url": device_url,
                                            "token": token, "expires_at": expires_at}, headers=headers)
    assert r.status_code == 200, r.text
    return token


def owner_poll(c, device_id, headers=SRC) -> dict:
    return c.get(f"/v0/device/{device_id}/messages", headers=headers).json()["owner"]


def test_register_redeem_poll_shows_paired_with_token_sha256(c):
    register(c, "irin-1", "123456")
    assert owner_poll(c, "irin-1") == {"state": "pending"}
    token = store.db()["owner_pairings"].find_one({"device_id": "irin-1"})["token"]
    r = c.post("/v0/device/pair", json={"code": "123456", "username": "Chris's phone"})
    assert r.status_code == 200
    body = r.json()
    assert body == {"device_id": "irin-1", "device_url": "http://localhost:8000", "token": token}
    poll = owner_poll(c, "irin-1")
    assert poll["state"] == "paired" and poll["username"] == "Chris's phone" and poll["paired_at"]
    assert poll["token_sha256"] == store.bearer_hash(token)


def test_unknown_used_and_expired_codes_are_404_with_the_same_message(c):
    unknown = c.post("/v0/device/pair", json={"code": "000000", "username": "X"})
    register(c, "irin-2", "222222")
    used_first = c.post("/v0/device/pair", json={"code": "222222", "username": "X"})
    assert used_first.status_code == 200
    used = c.post("/v0/device/pair", json={"code": "222222", "username": "Y"})
    register(c, "irin-3", "333333", minutes=-1)
    expired = c.post("/v0/device/pair", json={"code": "333333", "username": "Z"})
    for r in (unknown, used, expired):
        assert r.status_code == 404 and r.json()["detail"] == owner.BAD_CODE_DETAIL


def test_redeem_drops_the_plaintext_token(c):
    token = register(c, "irin-4", "444444")
    c.post("/v0/device/pair", json={"code": "444444", "username": "A"})
    doc = store.db()["owner_pairings"].find_one({"device_id": "irin-4"})
    assert "token" not in doc and "code" not in doc and "expires_at" not in doc
    assert doc["token_sha256"] == store.bearer_hash(token)


def test_unpair_by_bearer_and_by_source_key_and_401_unknown_bearer(c):
    assert c.delete("/v0/device/pair", headers={"Authorization": "Bearer nope"}).status_code == 401

    token_a = register(c, "irin-5", "555555")
    c.post("/v0/device/pair", json={"code": "555555", "username": "A"})
    r = c.delete("/v0/device/pair", headers={"Authorization": f"Bearer {token_a}"})
    assert r.status_code == 200 and r.json() == {"status": "revoked"}
    assert owner_poll(c, "irin-5")["state"] == "revoked"
    assert c.delete("/v0/device/pair", headers={"Authorization": f"Bearer {token_a}"}).json() == {"status": "revoked"}  # idempotent

    register(c, "irin-6", "666666", headers=SRC_B)
    c.post("/v0/device/pair", json={"code": "666666", "username": "B"})
    r2 = c.delete("/v0/device/pair", headers=SRC_B)
    assert r2.status_code == 200 and r2.json() == {"status": "revoked"}
    assert owner_poll(c, "irin-6", headers=SRC_B)["state"] == "revoked"
    assert c.delete("/v0/device/pair", headers=SRC_B).json() == {"status": "revoked"}  # idempotent


def test_a_second_redeem_revokes_the_first_token(c):
    token1 = register(c, "irin-7", "777771")
    c.post("/v0/device/pair", json={"code": "777771", "username": "First"})
    token2 = register(c, "irin-7", "777772")  # a fresh code while already paired: still reports paired
    assert owner_poll(c, "irin-7")["state"] == "paired" and owner_poll(c, "irin-7")["username"] == "First"
    r = c.post("/v0/device/pair", json={"code": "777772", "username": "Second"})
    assert r.status_code == 200 and r.json()["token"] == token2
    poll = owner_poll(c, "irin-7")
    assert poll["username"] == "Second" and poll["token_sha256"] == store.bearer_hash(token2)
    assert c.delete("/v0/device/pair", headers={"Authorization": f"Bearer {token1}"}).status_code == 401
    assert c.delete("/v0/device/pair", headers={"Authorization": f"Bearer {token2}"}).status_code == 200


def test_another_source_key_cannot_register_for_this_device(c):
    register(c, "irin-8", "888881")
    r = c.post("/v0/device/pairings", json={"code": "888882", "device_id": "irin-8", "device_url": "http://localhost:8000",
                                            "token": secrets.token_urlsafe(32),
                                            "expires_at": (store.now() + timedelta(minutes=10)).isoformat()},
              headers=SRC_B)
    assert r.status_code == 403
    assert c.post("/v0/device/pairings", json={"code": "888883", "device_id": "irin-8", "device_url": "http://localhost:8000",
                                               "token": secrets.token_urlsafe(32),
                                               "expires_at": (store.now() + timedelta(minutes=10)).isoformat()},
                 headers=SRC).status_code == 200  # the owning key still can


def test_log_never_carries_code_token_or_username(c):
    token = register(c, "irin-9", "999991")
    c.post("/v0/device/pair", json={"code": "999991", "username": "SecretPhoneName"})
    dump = json.dumps(c.get("/v0/log").json())
    for secret in ("999991", token, "SecretPhoneName"):
        assert secret not in dump
