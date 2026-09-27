"""Buddy v3 check: mutual matching (a real candidate who did not select you is
never offered; seeds always are), the app's hub (language filter by
casefold, the user's pool plus the samples, no forbidden field and never the
script in a row, 403 rules), the app claim (the script only in the claim
response, 409 for a second claimant, the holder gets its own claim back,
reopen on lease expiry), the samples never on the watcher's hub, and the 8
seeded sample listings (idempotent, wiped). Time moves by patching
store.now. Runs against the compose mongo in a throwaway database."""

import base64
import os
import sys
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from nacl.public import PrivateKey

os.environ.setdefault("ATLAS_URI", "mongodb://127.0.0.1:27017")
os.environ["RELAY_SOURCE_KEYS"] = "src-a,src-b"
os.environ["RELAY_DB_NAME"] = f"relay_test_{uuid.uuid4().hex[:8]}"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hub  # noqa: E402
import relay_api  # noqa: E402
import seed_buddies  # noqa: E402
import store  # noqa: E402
from main import app  # noqa: E402

KEYS = [f"v3-{i}" for i in range(6)]
FORBIDDEN = ("glucose", "mgdl", "mg_dl", "location", "lat", "lon", "phone", "email", "number", "script", "steps")


@pytest.fixture(scope="module", autouse=True)
def mongo():
    if store.status() != "ok":
        pytest.skip("no mongo at ATLAS_URI (start deploy/docker-compose.yml's mongo)")
    yield
    store.client().drop_database(store.DB_NAME)


@pytest.fixture
def c(monkeypatch):
    monkeypatch.setattr(relay_api, "RELAY_SOURCE_KEYS", ["src-a", "src-b", *KEYS])
    relay_api._hits.clear()
    store.client().drop_database(store.DB_NAME)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def clock(monkeypatch):
    state = {"t": store.now()}
    monkeypatch.setattr(store, "now", lambda: state["t"])
    return lambda seconds: state.update(t=state["t"] + timedelta(seconds=seconds))


def user(c, i, timezones, languages=("English",), is_demo=False, volunteer=True, have_buddy=True, verified=True):
    body = {"username": f"real{i}", "first_name": "Chris", "languages": list(languages), "timezones": timezones,
            "availability": [],
            "optins": {"have_buddy": have_buddy, "be_watcher": True, "hub_watchable": True, "hub_volunteer": volunteer},
            "cgm_verified": verified, "is_demo": is_demo}
    r = c.post("/v0/users", json=body, headers={"X-Source-Key": KEYS[i]})
    assert r.status_code == 200, r.text
    return r.json()["user_id"], {"Authorization": f"Bearer {r.json()['user_bearer']}"}


def device_listing(c, i, listing_id, is_demo=False):
    body = {"listing_id": listing_id, "first_name": "Rosa", "confidence": "unconfirmed", "elapsed_min": 6,
            "urgency": 3, "is_demo": is_demo, "event_id": f"ev-{listing_id}", "script": {"steps": ["Call Rosa."]}}
    assert c.post("/v0/hub/listing", json=body, headers={"X-Source-Key": KEYS[i]}).status_code == 200


def hub_ids(c, bearer):
    r = c.get("/v0/users/hub", headers=bearer)
    assert r.status_code == 200, r.text
    return [row["listing_id"] for row in r.json()]


# ---------------------------------------------------------------- mutual matching


def test_only_candidates_who_selected_you_are_offered(c):
    _, me = user(c, 0, ["America/New_York"])
    chose_me, _ = user(c, 1, ["Asia/Tokyo", "America/Chicago"])  # Chicago is 60 min from New York: selected
    user(c, 2, ["Asia/Tokyo", "America/Denver"])  # 120 min away: not selected
    user(c, 3, ["Asia/Tokyo"])  # picked no buddy zone: selects nobody
    assert [o["candidate_id"] for o in c.post("/v0/match", json={}, headers=me).json()] == [chose_me]


def test_seeds_are_always_eligible(c):
    seed_buddies.seed()
    _, me = user(c, 0, ["America/New_York"])  # no seed has a timezones[1]
    top = c.post("/v0/match", json={}, headers=me).json()
    assert len(top) == 3 and all(o["sample"] is True for o in top)


# ---------------------------------------------------------------- the app's hub


def test_hub_lists_by_shared_language_in_the_pool_plus_samples(c):
    seed_buddies.seed()
    assert seed_buddies.seed_listings() == 8
    _, en = user(c, 0, ["America/New_York"], languages=("english",))
    _, es = user(c, 1, ["America/New_York"], languages=("ENGLISH", "spanish"))
    _, demo = user(c, 2, ["America/New_York"], languages=("English", "Spanish"), is_demo=True)
    user(c, 3, ["America/Bogota"], languages=("Español", "Spanish"))
    device_listing(c, 3, "real-es")
    device_listing(c, 2, "demo-own", is_demo=True)
    samples = {d["listing_id"]: store.db()["users"].find_one({"source_key_hash": d["source_key_hash"]})["languages"]
               for d in store.db()["hub_listings"].find({"sample": True})}
    english = {k for k, v in samples.items() if "English" in v}
    spanish = {k for k, v in samples.items() if "Spanish" in v}
    assert set(hub_ids(c, en)) == english
    assert set(hub_ids(c, es)) == english | spanish | {"real-es"}
    assert set(hub_ids(c, demo)) == english | spanish  # the real listing is another pool; its own listing never shows
    rows = c.get("/v0/users/hub", headers=es).json()
    assert all(set(r) == {"listing_id", "first_name", "languages", "elapsed_min", "urgency", "confidence", "sample"}
               for r in rows)
    assert all(r["sample"] is (r["listing_id"] != "real-es") for r in rows)
    assert [r["urgency"] for r in rows] == sorted((r["urgency"] for r in rows), reverse=True)
    dump = str(rows).lower()
    assert not any(w in dump for w in FORBIDDEN) and "call" not in dump


def test_hub_403_rules(c):
    for i, kw in enumerate([{"verified": False}, {"have_buddy": False}, {"volunteer": False}]):
        _, bearer = user(c, i, ["America/New_York"], **kw)
        assert c.get("/v0/users/hub", headers=bearer).status_code == 403, kw
        assert c.post("/v0/users/hub/sample-01/claim", headers=bearer).status_code == 403, kw
    assert c.get("/v0/users/hub", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_claim_returns_the_script_once_and_409_for_a_second_claimant(c, clock):
    seed_buddies.seed()
    seed_buddies.seed_listings()
    a_id, a = user(c, 0, ["America/New_York"])
    b_id, b = user(c, 1, ["Europe/London"])
    r = c.post("/v0/users/hub/sample-01/claim", headers=a)
    assert r.status_code == 200 and set(r.json()) == {"claim_id", "expires_at", "script"}
    steps = r.json()["script"]["steps"]
    assert 3 <= len(steps) <= 5 and all(isinstance(s, str) and s for s in steps)
    assert "steps" not in str(c.get("/v0/users/hub", headers=a).json())  # never in a row, even for the holder
    second = c.post("/v0/users/hub/sample-01/claim", headers=b)
    assert second.status_code == 409 and second.json()["holder_expires_at"] == r.json()["expires_at"]
    again = c.post("/v0/users/hub/sample-01/claim", headers=a)  # the live holder gets its own claim back
    assert again.status_code == 200 and again.json()["claim_id"] == r.json()["claim_id"]
    claim = store.db()["hub_claims"].find_one({"claim_id": r.json()["claim_id"]})
    assert claim["volunteer_id"] == a_id and claim["actions"][:2] == ["claim", "script"]
    assert "hub.claim" in {row["route"] for row in c.get("/v0/log").json()}  # audited
    clock(hub.HUB_LEASE_S + 1)
    r2 = c.post("/v0/users/hub/sample-01/claim", headers=b)  # lease over: open again
    assert r2.status_code == 200 and r2.json()["script"]["steps"] == steps
    assert store.db()["hub_claims"].find_one({"claim_id": r.json()["claim_id"]})["outcome"] == "lease_expired"
    assert store.db()["hub_claims"].find_one({"claim_id": r2.json()["claim_id"]})["volunteer_id"] == b_id
    assert c.post("/v0/users/hub/nope/claim", headers=a).status_code == 404


def test_a_listing_you_cannot_see_cannot_be_claimed(c):
    seed_buddies.seed()
    seed_buddies.seed_listings()
    _, ja = user(c, 0, ["Asia/Tokyo"], languages=("Japanese",))
    no_shared = next(d["listing_id"] for d in store.db()["hub_listings"].find({"sample": True})
                     if "Japanese" not in store.db()["users"].find_one({"source_key_hash": d["source_key_hash"]})["languages"])
    assert c.post(f"/v0/users/hub/{no_shared}/claim", headers=ja).status_code == 404


def test_samples_never_reach_the_watchers_hub(c):
    seed_buddies.seed()
    seed_buddies.seed_listings()
    src = {"X-Source-Key": "src-a"}
    dev, peer = PrivateKey.generate(), PrivateKey.generate()
    b64 = lambda k: base64.b64encode(bytes(k.public_key)).decode()  # noqa: E731
    token = "cd" * 16
    assert c.post("/v0/pair", json={"token": token, "device_pk": b64(dev), "is_demo": False, "peer_kind": "buddy",
                                    "device_id": "irin-test"}, headers=src).status_code == 200
    c.post(f"/v0/pair/{token}/complete", json={"doctor_pk": b64(peer), "doctor_display_name": "Sam"})
    c.post(f"/v0/pair/{token}/confirm", headers=src)
    watcher = {"Authorization": f"Bearer {c.get(f'/v0/pair/{token}').json()['bearer']}"}
    assert c.get("/v0/hub/list", headers=watcher).json() == []
    assert c.post("/v0/hub/claim", json={"listing_id": "sample-01"}, headers=watcher).status_code == 404


# ---------------------------------------------------------------- the seeds


def test_seeds_create_exactly_8_listings_and_wipe_removes_them(c):
    real_id, real = user(c, 0, ["America/New_York"])
    device_listing(c, 0, "real-own")
    seed_buddies.seed()
    assert seed_buddies.seed_listings() == 8 and seed_buddies.seed_listings() == 8  # idempotent
    rows = list(store.db()["hub_listings"].find({"sample": True}))
    assert sorted(d["listing_id"] for d in rows) == [f"sample-0{i}" for i in range(1, 9)]
    seeds = {u["source_key_hash"]: u for u in store.db()["users"].find({"seed": True})}
    people = [seeds[d["source_key_hash"]] for d in rows]
    assert len({p["user_id"] for p in people}) == 8 and "sam_sample" not in {p["username"] for p in people}
    assert sum("English" in p["languages"] for p in people) >= 5
    assert len({lang for p in people for lang in p["languages"]}) >= 6
    assert all(d["is_demo"] is False and d["status"] == "open" and d["urgency"] in (1, 2)
               and 3 <= d["elapsed_min"] <= 25 and d["script_ciphertext"] and "script" not in d for d in rows)
    assert {d["confidence"] for d in rows} == {"device_confirmed", "unconfirmed"}
    assert all(3 <= len(hub.open_script(d)) <= 5 for d in rows)  # sealed like a device's script
    c.post("/v0/users/hub/sample-01/claim", headers=real)
    assert seed_buddies.wipe() == 150
    assert store.db()["hub_listings"].count_documents({"sample": True}) == 0
    assert [d["listing_id"] for d in store.db()["hub_listings"].find()] == ["real-own"]
    assert store.db()["hub_claims"].count_documents({}) == 0
    assert real_id in {u["user_id"] for u in store.db()["users"].find()}
