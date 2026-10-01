"""Phone-only accounts (Phase 1) check, against the pinned contract in
relay/README.md: POST /v0/users/phone issues a random bearer stored only as a
hash (5/min/IP), 422 for cgm_verified, is_demo, _sample and forbidden fields;
GET and PUT /v0/users/me (PUT never touches the server-set fields); the cloud
marks an account verified with X-Cloud-Key AND the user's own bearer (503
unset, 401 wrong); /v0/device/pair/check answers only for the device's
ACTIVE owner token and never stores it; /v0/users/link's four cases and its
404/409 texts, after which the Pi's own POST /v0/users upserts the SAME
document and both bearers keep working; and a phone user matches, accepts,
reads the hub and claims exactly as a device user does. Runs against the
compose mongo in a throwaway database; skips without a server."""

import json
import os
import secrets
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("ATLAS_URI", "mongodb://127.0.0.1:27017")
os.environ["RELAY_SOURCE_KEYS"] = "src-a,src-b"
os.environ["RELAY_DB_NAME"] = f"relay_test_{uuid.uuid4().hex[:8]}"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import owner  # noqa: E402
import relay_api  # noqa: E402
import seed_buddies  # noqa: E402
import store  # noqa: E402
from main import app  # noqa: E402

KEYS = [f"ph-{i}" for i in range(4)]
CLOUD = {"X-Cloud-Key": "cloud-test-key"}
PROFILE = {"username": "chris_phone", "first_name": "Chris", "languages": ["English"],
           "timezones": ["America/New_York"], "availability": [],
           "optins": {"have_buddy": True, "be_watcher": True, "hub_watchable": False, "hub_volunteer": True}}
ME_KEYS = {"user_id", "username", "first_name", "languages", "timezones", "availability", "optins",
           "cgm_verified", "is_demo", "phone", "device_linked"}
NOT_PAIRED = "that phone is not paired with this Irin"
BOTH_HAVE_BUDDIES = "both profiles already have buddies; disconnect one first"


@pytest.fixture(scope="module", autouse=True)
def mongo():
    if store.status() != "ok":
        pytest.skip("no mongo at ATLAS_URI (start deploy/docker-compose.yml's mongo)")
    yield
    store.client().drop_database(store.DB_NAME)


@pytest.fixture
def c(monkeypatch):
    monkeypatch.setattr(relay_api, "RELAY_SOURCE_KEYS", ["src-a", "src-b", *KEYS])
    monkeypatch.setattr(relay_api, "RELAY_CLOUD_KEY", CLOUD["X-Cloud-Key"])
    relay_api._hits.clear()
    owner._redeems.clear()
    store.client().drop_database(store.DB_NAME)
    with TestClient(app) as client:
        yield client


def phone(c, username="chris_phone", **over):
    r = c.post("/v0/users/phone", json={**PROFILE, "username": username, **over})
    assert r.status_code == 200, r.text
    return r.json()["user_id"], {"Authorization": f"Bearer {r.json()['user_bearer']}"}


def device_user(c, i, username=None, verified=True, is_demo=False):
    body = {**PROFILE, "username": username or f"pi{i}", "cgm_verified": verified, "is_demo": is_demo}
    r = c.post("/v0/users", json=body, headers={"X-Source-Key": KEYS[i]})
    assert r.status_code == 200, r.text
    return r.json()["user_id"], {"Authorization": f"Bearer {r.json()['user_bearer']}"}


def pair_device(c, i, device_id, code, username="Chris's phone") -> str:
    """Register (Pi, source key i) and redeem (app): the device's active owner token."""
    token = secrets.token_urlsafe(32)
    r = c.post("/v0/device/pairings", json={"code": code, "device_id": device_id, "device_url": "http://localhost:8000",
                                            "token": token, "expires_at": (store.now() + timedelta(minutes=10)).isoformat()},
               headers={"X-Source-Key": KEYS[i]})
    assert r.status_code == 200, r.text
    r = c.post("/v0/device/pair", json={"code": code, "username": username})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def verify(c, user_id, bearer):
    r = c.post(f"/v0/users/{user_id}/verified", json={}, headers={**CLOUD, **bearer})
    assert r.status_code == 200, r.text
    return r.json()


def me(c, bearer) -> dict:
    r = c.get("/v0/users/me", headers=bearer)
    assert r.status_code == 200, r.text
    return r.json()


def db_dump() -> str:
    return str([list(store.db()[name].find()) for name in store.db().list_collection_names()])


# ---------------------------------------------------------------- sign-up


def test_phone_signup_issues_a_random_bearer_stored_only_as_a_hash(c):
    uid, bearer = phone(c)
    plain = bearer["Authorization"].split(" ", 1)[1]
    doc = store.db()["users"].find_one({"user_id": uid})
    assert doc["phone"] is True and doc["cgm_verified"] is False and doc["is_demo"] is False and doc["created_at"]
    assert doc["phone_bearer_hash"] == store.bearer_hash(plain) and "bearer_hash" not in doc
    assert len(doc["source_key_hash"]) == 64  # random, like a seed's: no device key ever resolves to it
    assert plain not in db_dump()
    uid2, bearer2 = phone(c, username="chris_other", **{"first_name": "Other"})
    doc2 = store.db()["users"].find_one({"user_id": uid2})
    assert uid2 != uid and bearer2 != bearer and doc2["source_key_hash"] != doc["source_key_hash"]
    assert me(c, bearer) == {"user_id": uid, **PROFILE, "cgm_verified": False, "is_demo": False,
                             "phone": True, "device_linked": False}


@pytest.mark.parametrize("over", [{"cgm_verified": True}, {"cgm_verified": False}, {"is_demo": False},
                                  {"username": "sam_sample"}, {"glucose": 90}, {"phone_number": "+1"},
                                  {"anything_else": 1}])
def test_phone_signup_422s(c, over):
    assert c.post("/v0/users/phone", json={**PROFILE, **over}).status_code == 422
    assert store.db()["users"].count_documents({}) == 0


def test_phone_signup_is_capped_at_5_per_minute_per_ip(c):
    codes = [c.post("/v0/users/phone", json={**PROFILE, "username": f"user{i}"}).status_code for i in range(6)]
    assert codes == [200] * 5 + [429]
    other = c.post("/v0/users/phone", json=PROFILE, headers={"X-Forwarded-For": "10.9.8.7"})
    assert other.status_code == 200  # another IP has its own bucket


def test_phone_signup_is_capped_at_30_per_minute_overall(c):
    codes = [c.post("/v0/users/phone", json={**PROFILE, "username": f"user{i}"},
                    headers={"X-Forwarded-For": f"10.0.{i // 250}.{i % 250}"}).status_code for i in range(31)]
    assert codes == [200] * 30 + [429]  # rotating IPs buys nothing


# ---------------------------------------------------------------- /v0/users/me


def test_me_put_changes_the_profile_and_never_the_server_set_fields(c):
    uid, bearer = phone(c)
    verify(c, uid, bearer)
    before = store.db()["users"].find_one({"user_id": uid})
    new = {**PROFILE, "username": "chris_renamed", "languages": ["English", "Spanish"],
           "optins": {**PROFILE["optins"], "have_buddy": False}}
    r = c.put("/v0/users/me", json=new, headers=bearer)
    assert r.status_code == 200, r.text
    assert r.json() == {"user_id": uid, **new, "cgm_verified": True, "is_demo": False, "phone": True, "device_linked": False}
    assert me(c, bearer) == r.json()
    after = store.db()["users"].find_one({"user_id": uid})
    for k in ("user_id", "cgm_verified", "is_demo", "phone_bearer_hash", "source_key_hash", "created_at"):
        assert after[k] == before[k], k
    assert "bearer_hash" not in after
    for bad in ({"cgm_verified": False}, {"is_demo": True}, {"glucose": 1}):
        assert c.put("/v0/users/me", json={**new, **bad}, headers=bearer).status_code == 422, bad
    assert c.put("/v0/users/me", json={**new, "username": "x_sample"}, headers=bearer).status_code == 422
    assert c.get("/v0/users/me", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert c.get("/v0/users/me").status_code == 401


def test_me_for_a_device_user_reports_device_linked_once_an_owner_pairing_names_its_key(c):
    uid, bearer = device_user(c, 0)
    assert me(c, bearer) == {"user_id": uid, **PROFILE, "username": "pi0", "cgm_verified": True, "is_demo": False,
                             "phone": False, "device_linked": False}
    pair_device(c, 0, "irin-me", "111111")
    assert me(c, bearer)["device_linked"] is True
    assert c.delete("/v0/device/pair", headers={"X-Source-Key": KEYS[0]}).status_code == 200
    assert me(c, bearer)["device_linked"] is False  # a revoked pairing is not a link
    assert c.put("/v0/users/me", json=PROFILE, headers=bearer).json()["cgm_verified"] is True  # still the Pi's word


# ---------------------------------------------------------------- the cloud's verified mark


def test_verified_needs_the_cloud_key_and_the_users_own_bearer(c, monkeypatch):
    uid, bearer = phone(c)
    other, other_bearer = phone(c, username="someone_else")
    url = f"/v0/users/{uid}/verified"
    assert c.post(url, json={}, headers={"X-Cloud-Key": "wrong", **bearer}).status_code == 401
    assert c.post(url, json={}, headers=bearer).status_code == 401  # no cloud key at all
    assert c.post(url, json={}, headers=CLOUD).status_code == 401  # no bearer
    assert c.post(url, json={}, headers={**CLOUD, "Authorization": "Bearer nope"}).status_code == 401
    assert c.post(url, json={}, headers={**CLOUD, **other_bearer}).status_code == 404  # someone else's bearer
    assert c.post("/v0/users/u-nope/verified", json={}, headers={**CLOUD, **bearer}).status_code == 404
    assert me(c, bearer)["cgm_verified"] is False
    out = verify(c, uid, bearer)
    assert set(out) == {"user_id", "cgm_verified", "verified_at"} and out["user_id"] == uid and out["cgm_verified"] is True
    doc = store.db()["users"].find_one({"user_id": uid})
    assert doc["cgm_verified"] is True
    assert abs(doc["verified_at"] - datetime.fromisoformat(out["verified_at"])) < timedelta(milliseconds=1)  # mongo keeps ms
    assert me(c, bearer)["cgm_verified"] is True and me(c, other_bearer)["cgm_verified"] is False
    rows = [r for r in c.get("/v0/log").json() if r["route"] == "directory.verified"]
    assert len(rows) == 1 and set(rows[0]) == {"at", "route", "user_id"} and rows[0]["user_id"] == uid
    monkeypatch.setattr(relay_api, "RELAY_CLOUD_KEY", "")
    monkeypatch.delenv("RELAY_CLOUD_KEY", raising=False)
    assert c.post(url, json={}, headers={**CLOUD, **bearer}).status_code == 503


# ---------------------------------------------------------------- the owner-token check for the cloud


def test_pair_check_answers_only_for_the_active_owner_token_and_never_keeps_it(c, monkeypatch):
    token = pair_device(c, 0, "irin-chk", "222222", username="Chris's phone")
    check = lambda body, headers=CLOUD: c.post("/v0/device/pair/check", json=body, headers=headers)  # noqa: E731
    r = check({"device_id": "irin-chk", "token": token})
    assert r.status_code == 200 and r.json() == {"ok": True, "username": "Chris's phone"}
    assert check({"device_id": "irin-chk", "token": secrets.token_urlsafe(32)}).json() == {"ok": False, "username": None}
    assert check({"device_id": "irin-nope", "token": token}).json() == {"ok": False, "username": None}
    # a fresh code minted while paired: its pending token is NOT the active owner
    pending = secrets.token_urlsafe(32)
    assert c.post("/v0/device/pairings", json={"code": "222223", "device_id": "irin-chk", "device_url": "http://localhost:8000",
                                               "token": pending, "expires_at": (store.now() + timedelta(minutes=10)).isoformat()},
                  headers={"X-Source-Key": KEYS[0]}).status_code == 200
    assert check({"device_id": "irin-chk", "token": pending}).json() == {"ok": False, "username": None}
    assert check({"device_id": "irin-chk", "token": token}).json()["ok"] is True
    # revoked: no longer active
    assert c.delete("/v0/device/pair", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert check({"device_id": "irin-chk", "token": token}).json() == {"ok": False, "username": None}
    # the token is never logged; the check stores nothing of it (only the pending plaintext the Pi registered)
    assert token not in json.dumps(c.get("/v0/log").json()) and token not in db_dump()
    assert check({"device_id": "irin-chk", "token": token}, headers={"X-Cloud-Key": "wrong"}).status_code == 401
    assert check({"device_id": "irin-chk", "token": token}, headers={}).status_code == 401
    assert check({"device_id": "irin-chk"}).status_code == 422
    monkeypatch.setattr(relay_api, "RELAY_CLOUD_KEY", "")
    monkeypatch.delenv("RELAY_CLOUD_KEY", raising=False)
    assert check({"device_id": "irin-chk", "token": token}).status_code == 503


# ---------------------------------------------------------------- linking a phone account to a device


def link(c, bearer, device_id, token):
    return c.post("/v0/users/link", json={"device_id": device_id, "owner_token": token}, headers=bearer)


def link_cases() -> list[str]:
    return [r["case"] for r in store.db()["audit"].find({"route": "directory.link"}).sort("at", 1)]


def test_link_case_a_then_the_pi_upserts_the_same_document(c):
    uid, bearer = phone(c)
    token = pair_device(c, 0, "irin-a", "333333")
    r = link(c, bearer, "irin-a", token)
    assert r.status_code == 200 and r.json() == {"user_id": uid, "device_linked": True}
    doc = store.db()["users"].find_one({"user_id": uid})
    assert doc["source_key_hash"] == store.bearer_hash(KEYS[0]) and doc["phone"] is True
    assert me(c, bearer)["device_linked"] is True
    assert link(c, bearer, "irin-a", token).status_code == 200  # linking again changes nothing
    assert store.db()["users"].count_documents({}) == 1
    # the Pi's own POST /v0/users now upserts THE SAME document
    pi_uid, pi_bearer = device_user(c, 0, username="chris_pi", verified=True)
    assert pi_uid == uid and store.db()["users"].count_documents({}) == 1
    after = store.db()["users"].find_one({"user_id": uid})
    assert after["phone_bearer_hash"] == doc["phone_bearer_hash"] and after["phone"] is True
    assert after["bearer_hash"] == store.bearer_hash(pi_bearer["Authorization"].split(" ", 1)[1])
    assert after["username"] == "chris_pi" and after["cgm_verified"] is True
    assert me(c, bearer) == me(c, pi_bearer) == {"user_id": uid, **PROFILE, "username": "chris_pi", "cgm_verified": True,
                                                 "is_demo": False, "phone": True, "device_linked": True}
    rows = [r for r in store.db()["audit"].find({"route": "directory.link"})]
    assert all(set(r) >= {"user_id", "device_user_id", "case"} for r in rows) and rows[0]["case"] == "a"
    assert token not in json.dumps(c.get("/v0/log").json())


def test_link_404_for_a_token_that_is_not_the_devices_active_one_and_403_for_a_device_bearer(c):
    uid, bearer = phone(c)
    token = pair_device(c, 0, "irin-x", "444444")
    for body in ({"device_id": "irin-x", "token": secrets.token_urlsafe(32)}, {"device_id": "irin-nope", "token": token}):
        r = link(c, bearer, body["device_id"], body["token"])
        assert r.status_code == 404 and r.json()["detail"] == NOT_PAIRED
    assert c.delete("/v0/device/pair", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    r = link(c, bearer, "irin-x", token)
    assert r.status_code == 404 and r.json()["detail"] == NOT_PAIRED  # revoked is not active
    assert me(c, bearer)["device_linked"] is False
    _, pi_bearer = device_user(c, 1)
    token1 = pair_device(c, 1, "irin-y", "444445")
    assert link(c, pi_bearer, "irin-y", token1).status_code == 403  # a device-derived bearer never links
    assert c.post("/v0/users/link", json={"device_id": "irin-y", "owner_token": token1}).status_code == 401
    assert link(c, bearer, "irin-y", token1).status_code == 200  # the phone bearer may link to a second device later
    assert link_cases() == ["b"]  # the device's document had no matches: deleted, then (a)


def test_link_case_b_deletes_a_device_document_without_a_buddy_and_keeps_the_pis_bearer_working(c):
    pi_uid, pi_bearer = device_user(c, 0)
    token = pair_device(c, 0, "irin-b", "555555")
    uid, bearer = phone(c)
    r = link(c, bearer, "irin-b", token)
    assert r.status_code == 200 and r.json() == {"user_id": uid, "device_linked": True}
    assert store.db()["users"].find_one({"user_id": pi_uid}) is None
    assert store.db()["users"].count_documents({}) == 1
    assert me(c, bearer)["device_linked"] is True and me(c, bearer)["username"] == "chris_phone"
    # the Pi caches its bearer (HMAC of the OLD user_id) and re-POSTs only when a human re-saves: it must still work
    doc = store.db()["users"].find_one({"user_id": uid})
    assert doc["bearer_hash"] == store.bearer_hash(pi_bearer["Authorization"].split(" ", 1)[1])
    assert doc["cgm_verified"] is True and doc["is_demo"] is False and doc["phone"] is True
    assert me(c, pi_bearer) == me(c, bearer) and me(c, pi_bearer)["cgm_verified"] is True
    assert c.post("/v0/match", json={}, headers=pi_bearer).status_code == 200
    assert c.post("/v0/match", json={}, headers=bearer).status_code == 200
    row = store.db()["audit"].find_one({"route": "directory.link"})
    assert row["case"] == "b" and row["user_id"] == uid and row["device_user_id"] == pi_uid


def test_offered_only_rows_on_both_sides_link_fine_and_the_orphans_are_deleted(c):
    seed_buddies.seed()
    pi_uid, pi_bearer = device_user(c, 0)
    token = pair_device(c, 0, "irin-o", "555556")
    pi_offers = c.post("/v0/match", json={}, headers=pi_bearer).json()
    uid, bearer = phone(c)
    verify(c, uid, bearer)
    mine = c.post("/v0/match", json={}, headers=bearer).json()
    assert len(pi_offers) == len(mine) == 3
    r = link(c, bearer, "irin-o", token)  # case (b): offered rows are not buddies
    assert r.status_code == 200 and r.json() == {"user_id": uid, "device_linked": True}
    assert link_cases() == ["b"]
    assert store.db()["matches"].count_documents({"users": pi_uid}) == 0  # no row names the deleted document
    assert sorted(m["match_id"] for m in store.db()["matches"].find({"users": uid})) == sorted(o["match_id"] for o in mine)
    assert [m["match_id"] for m in c.get("/v0/users/matches", headers=bearer).json()] == [o["match_id"] for o in mine]
    # case (a) with offered rows on the phone: nothing is deleted
    token2 = pair_device(c, 1, "irin-o2", "555557")
    assert link(c, bearer, "irin-o2", token2).status_code == 200 and link_cases() == ["b", "a"]
    assert store.db()["matches"].count_documents({"users": uid}) == 3


def test_link_409_try_again_when_the_key_move_collides(c, monkeypatch):
    from pymongo.collection import Collection
    from pymongo.errors import DuplicateKeyError
    uid, bearer = phone(c)
    token = pair_device(c, 0, "irin-dup", "555558")
    real = Collection.update_one

    def colliding(self, flt, update, *a, **kw):
        if "source_key_hash" in update.get("$set", {}):  # the Pi's upsert landed first
            raise DuplicateKeyError("E11000 duplicate key")
        return real(self, flt, update, *a, **kw)
    monkeypatch.setattr(Collection, "update_one", colliding)
    r = link(c, bearer, "irin-dup", token)
    assert r.status_code == 409 and r.json()["detail"] == "try again"
    assert store.db()["audit"].count_documents({"route": "directory.link"}) == 0


def test_link_case_c_the_phone_adopts_a_device_document_with_a_buddy(c):
    seed_buddies.seed()
    pi_uid, pi_bearer = device_user(c, 0)
    token = pair_device(c, 0, "irin-c", "666666")
    offers = c.post("/v0/match", json={}, headers=pi_bearer).json()
    assert len(offers) == 3
    assert c.post(f"/v0/match/{offers[0]['match_id']}/accept", headers=pi_bearer).json()["status"] == "accepted"
    uid, bearer = phone(c)
    verify(c, uid, bearer)
    assert len(c.post("/v0/match", json={}, headers=bearer).json()) == 3  # offered only: not a buddy
    plain = bearer["Authorization"].split(" ", 1)[1]
    r = link(c, bearer, "irin-c", token)
    assert r.status_code == 200 and r.json() == {"user_id": pi_uid, "device_linked": True}
    assert store.db()["users"].find_one({"user_id": uid}) is None
    doc = store.db()["users"].find_one({"user_id": pi_uid})
    assert doc["phone"] is True and doc["phone_bearer_hash"] == store.bearer_hash(plain)
    assert doc["bearer_hash"] == store.bearer_hash(pi_bearer["Authorization"].split(" ", 1)[1])
    assert me(c, bearer)["user_id"] == pi_uid and me(c, pi_bearer)["user_id"] == pi_uid
    assert me(c, bearer)["username"] == "pi0" and me(c, bearer)["device_linked"] is True
    assert store.db()["matches"].count_documents({"users": uid}) == 0  # the phone's offered rows went with its document
    rows = c.get("/v0/users/matches", headers=bearer).json()
    assert [m["match_id"] for m in rows] == [o["match_id"] for o in offers]
    assert [m["status"] for m in rows] == ["accepted", "offered", "offered"]
    again = c.post("/v0/match", json={}, headers=bearer).json()
    assert [o["match_id"] for o in again][:2] == [o["match_id"] for o in offers[1:]]  # the accepted one never re-offered
    row = store.db()["audit"].find_one({"route": "directory.link"})
    assert row["case"] == "c" and row["user_id"] == uid and row["device_user_id"] == pi_uid


def test_link_case_d_409_when_both_have_a_buddy(c):
    seed_buddies.seed()
    pi_uid, pi_bearer = device_user(c, 0)
    token = pair_device(c, 0, "irin-d", "777777")
    pi_offers = c.post("/v0/match", json={}, headers=pi_bearer).json()
    assert c.post(f"/v0/match/{pi_offers[0]['match_id']}/accept", headers=pi_bearer).json()["status"] == "accepted"
    uid, bearer = phone(c)
    verify(c, uid, bearer)
    mine = c.post("/v0/match", json={}, headers=bearer).json()
    assert c.post(f"/v0/match/{mine[0]['match_id']}/accept", headers=bearer).json()["status"] == "accepted"
    before = db_dump()
    r = link(c, bearer, "irin-d", token)
    assert r.status_code == 409 and r.json()["detail"] == BOTH_HAVE_BUDDIES
    assert db_dump() == before  # nothing changed on either side
    assert me(c, bearer)["user_id"] == uid and me(c, pi_bearer)["user_id"] == pi_uid


# ---------------------------------------------------------------- a phone user on the existing routes


def test_a_verified_phone_user_matches_accepts_and_works_the_hub(c):
    seed_buddies.seed()
    assert seed_buddies.seed_listings() == 8
    uid, bearer = phone(c)
    assert c.post("/v0/match", json={}, headers=bearer).status_code == 403  # unverified
    assert c.get("/v0/users/hub", headers=bearer).status_code == 403
    verify(c, uid, bearer)
    offers = c.post("/v0/match", json={}, headers=bearer).json()
    assert len(offers) == 3 and all(o["sample"] is True and o["status"] == "offered" for o in offers)
    sam = store.db()["users"].find_one({"username": "sam_sample"})
    assert offers[0]["candidate_id"] == sam["user_id"]  # a New York night: Sam is always the top match
    r = c.post(f"/v0/match/{offers[0]['match_id']}/accept", headers=bearer)
    assert r.status_code == 200 and r.json()["status"] == "accepted"
    rows = c.get("/v0/users/hub", headers=bearer).json()
    assert rows and all(r["sample"] is True for r in rows)
    r = c.post(f"/v0/users/hub/{rows[0]['listing_id']}/claim", headers=bearer)
    assert r.status_code == 200 and r.json()["script"]["steps"]
    assert store.db()["hub_claims"].find_one({"claim_id": r.json()["claim_id"]})["volunteer_id"] == uid
    assert c.get("/v0/users/search?username=sam", headers=bearer).json()[0]["username"] == "sam_sample"


# ---------------------------------------------------------------- GET /v0/users/matches


def test_users_matches_lists_my_rows_with_every_status_and_never_anothers(c):
    seed_buddies.seed()
    uid, bearer = phone(c)
    assert c.get("/v0/users/matches", headers=bearer).json() == []  # unverified: still readable, just empty
    verify(c, uid, bearer)
    offers = c.post("/v0/match", json={}, headers=bearer).json()
    rows = c.get("/v0/users/matches", headers=bearer).json()
    assert [r["match_id"] for r in rows] == [o["match_id"] for o in offers]  # newest last = the offer order
    assert all(set(r) == {"match_id", "candidate_id", "first_name", "status", "score", "hours_covered", "mirror",
                          "shared_languages", "sample"} and r["sample"] is True and r["status"] == "offered" for r in rows)
    assert "pair_url" not in str(rows)
    c.post(f"/v0/match/{offers[0]['match_id']}/accept", headers=bearer)
    c.post(f"/v0/match/{offers[1]['match_id']}/decline", headers=bearer)
    by_id = {r["match_id"]: r for r in c.get("/v0/users/matches", headers=bearer).json()}
    assert by_id[offers[0]["match_id"]]["status"] == "accepted" and by_id[offers[1]["match_id"]]["status"] == "declined"
    assert by_id[offers[2]["match_id"]]["status"] == "offered" and by_id[offers[0]["match_id"]]["first_name"] == "Sam"
    other_id, other = phone(c, username="someone_else")
    verify(c, other_id, other)
    assert c.get("/v0/users/matches", headers=other).json() == []  # never another user's rows
    theirs = c.post("/v0/match", json={}, headers=other).json()
    assert {r["match_id"] for r in c.get("/v0/users/matches", headers=other).json()} == {o["match_id"] for o in theirs}
    assert not {o["match_id"] for o in theirs} & set(by_id)
    _, pi_bearer = device_user(c, 0)  # a device user reads the same route
    assert c.get("/v0/users/matches", headers=pi_bearer).json() == []
    assert c.get("/v0/users/matches", headers={"Authorization": "Bearer nope"}).status_code == 401
    audit = [r for r in c.get("/v0/log").json() if r["route"] == "directory.matches"]
    assert audit and set(audit[0]) == {"at", "route", "user_id", "count"}
