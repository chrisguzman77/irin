"""Buddy onboarding v2 check: the +3 preferred-zone bonus (timezones[1]),
case-insensitive languages, the 150 seeded sample profiles (idempotent,
--wipe), Sam as the top match for a New York night that prefers Tokyo, a
seed accepting at once, `sample: true` on every row naming a seed, and no
seed ever posting a pair link. Runs against the compose mongo in a throwaway
database."""

import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("ATLAS_URI", "mongodb://127.0.0.1:27017")
os.environ["RELAY_SOURCE_KEYS"] = "src-a,src-b"
os.environ["RELAY_DB_NAME"] = f"relay_test_{uuid.uuid4().hex[:8]}"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import directory  # noqa: E402
import relay_api  # noqa: E402
import seed_buddies  # noqa: E402
import store  # noqa: E402
from main import app  # noqa: E402

KEYS = [f"v2-{i}" for i in range(4)]
AT = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)  # no DST anywhere below
OFFICE = [{"weekday": d, "start": "09:00", "end": "17:00"} for d in range(7)]


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


def profile(tz, prefer=None, languages=("English",), availability=OFFICE):
    return {"timezones": [tz, prefer] if prefer else [tz], "languages": list(languages),
            "availability": list(availability)}


def user(c, i, timezones, languages=("English",), have_buddy=True, username=None):
    body = {"username": username or f"real{i}", "first_name": "Chris", "languages": list(languages),
            "timezones": timezones, "availability": [],
            "optins": {"have_buddy": have_buddy, "be_watcher": True, "hub_watchable": False, "hub_volunteer": False},
            "cgm_verified": True, "is_demo": False}
    r = c.post("/v0/users", json=body, headers={"X-Source-Key": KEYS[i]})
    assert r.status_code == 200, r.text
    return r.json()["user_id"], {"Authorization": f"Bearer {r.json()['user_bearer']}"}


def test_preferred_zone_adds_three_within_sixty_minutes():
    plain = directory.score(profile("America/Bogota"), profile("Asia/Tokyo"), AT)
    for zone, bonus in [("Asia/Tokyo", 3), ("Asia/Seoul", 3), ("Asia/Shanghai", 3), ("Asia/Bangkok", 0)]:
        got = directory.score(profile("America/Bogota", prefer="Asia/Tokyo"), profile(zone), AT)
        base = directory.score(profile("America/Bogota"), profile(zone), AT)
        assert got["score"] == base["score"] + bonus, zone
        assert {k: v for k, v in got.items() if k != "score"} == {k: v for k, v in base.items() if k != "score"}
    assert plain["score"] == directory.score(profile("America/Bogota"), profile("Asia/Tokyo"), AT)["score"]
    # the candidate's HOME zone counts, never its own preferred zone
    assert directory.score(profile("America/Bogota", prefer="Asia/Tokyo"),
                           profile("Europe/London", prefer="Asia/Tokyo"), AT)["score"] == \
        directory.score(profile("America/Bogota"), profile("Europe/London"), AT)["score"]


def test_languages_match_case_insensitively_and_are_stored_as_sent(c):
    got = directory.score(profile("America/Bogota", languages=("English", "english", "ES", "fr")),
                          profile("Asia/Tokyo", languages=("ENGLISH", "es")), AT)
    assert got["shared_languages"] == ["ES", "English"]
    assert got["score"] == directory.score(profile("America/Bogota"), profile("Asia/Tokyo", languages=()), AT)["score"] + 2
    user(c, 0, ["America/Bogota"], languages=("EnGlish", "ES"))
    assert store.db()["users"].find_one({"username": "real0"})["languages"] == ["EnGlish", "ES"]


def test_seeding_150_is_idempotent_and_wipe_removes_every_seed(c):
    real_id, _ = user(c, 0, ["America/New_York"])
    assert seed_buddies.seed() == 150
    seeds = list(store.db()["users"].find({"seed": True}))
    assert len(seeds) == 150 and len({s["username"] for s in seeds}) == 150
    assert all(s["username"].endswith("_sample") and s["cgm_verified"] and s["optins"]["be_watcher"]
               and s["is_demo"] is False for s in seeds)
    assert len({s["source_key_hash"] for s in seeds}) == 150
    assert len({s["timezones"][0] for s in seeds}) >= 25
    for busy in ("Asia/Tokyo", "Europe/London", "Australia/Sydney", "America/Los_Angeles"):
        assert sum(s["timezones"][0] == busy for s in seeds) >= 12, busy
    assert {s["optins"]["have_buddy"] for s in seeds} == {True, False}
    for s in seeds:  # every seed passes the same model POST /v0/users uses
        directory.UserIn(**{**{k: s[k] for k in directory.UserIn.model_fields}, "username": s["username"][:-7] + "_s"})
    ids = {s["username"]: (s["user_id"], s["source_key_hash"]) for s in seeds}
    assert seed_buddies.seed() == 150
    again = {s["username"]: (s["user_id"], s["source_key_hash"]) for s in store.db()["users"].find({"seed": True})}
    assert again == ids
    sam = store.db()["users"].find_one({"username": "sam_sample"})
    assert sam["first_name"] == "Sam" and sam["timezones"] == ["Asia/Tokyo"]
    assert sam["languages"] == ["English", "Japanese"]
    assert sam["availability"] == [{"weekday": d, "start": "08:00", "end": "18:00"} for d in range(7)]
    assert seed_buddies.wipe() == 150
    assert store.db()["users"].count_documents({"seed": True}) == 0
    assert [u["user_id"] for u in store.db()["users"].find()] == [real_id]


def test_sam_is_the_top_match_for_a_new_york_night_preferring_tokyo(c):
    seed_buddies.seed()
    _, me = user(c, 0, ["America/New_York", "Asia/Tokyo"], languages=("English",))
    top = c.post("/v0/match", json={}, headers=me).json()
    assert len(top) == 3 and top[0]["first_name"] == "Sam" and top[0]["sample"] is True
    assert top[0]["shared_languages"] == ["English"] and top[0]["mirror"] is True
    assert top[0]["score"] > top[1]["score"]
    assert all(o["sample"] is True for o in top)


def test_a_seed_accepts_at_once_and_never_posts_a_pair_link(c):
    seed_buddies.seed()
    me_id, me = user(c, 0, ["America/New_York", "Asia/Tokyo"])
    sam = c.post("/v0/match", json={}, headers=me).json()[0]
    r = c.post(f"/v0/match/{sam['match_id']}/accept", headers=me)
    assert r.status_code == 200 and r.json()["status"] == "accepted" and r.json()["sample"] is True
    m = store.db()["matches"].find_one({"match_id": sam["match_id"]})
    assert set(m["accepted_by"]) == set(m["users"])
    url = "https://watch.irin.test/pair#token=" + "ab" * 16
    assert c.post(f"/v0/match/{sam['match_id']}/pair_link", json={"pair_url": url},
                  headers={"X-Source-Key": KEYS[0]}).status_code == 200  # the real side may still post its own
    poll = c.get("/v0/device/irin-a/messages", headers={"X-Source-Key": KEYS[0]}).json()["matches"]
    assert [r for r in poll if r["match_id"] == sam["match_id"]] == [
        {"match_id": sam["match_id"], "first_name": "Sam", "status": "accepted", "pair_url": None, "sample": True}]
    assert all(r["sample"] is True and r["pair_url"] is None for r in poll)  # the other two offers are seeds too
    seed_ids = {s["user_id"] for s in store.db()["users"].find({"seed": True})}
    assert not any(set(x.get("pair_urls", {})) & seed_ids for x in store.db()["matches"].find())
    assert me_id in store.db()["matches"].find_one({"match_id": sam["match_id"]})["pair_urls"]


def test_sample_marks_seed_rows_only(c):
    seed_buddies.seed()
    _, me = user(c, 0, ["America/New_York"])
    user(c, 1, ["Asia/Tokyo"], username="samreal")
    rows = c.get("/v0/users/search", params={"username": "sam"}, headers=me).json()
    by_name = {r["username"]: r for r in rows}
    assert by_name["sam_sample"]["sample"] is True
    assert "sample" not in by_name["samreal"]  # a real row keeps its exact shape
    offers = c.post("/v0/match", json={}, headers=me).json()
    real = next((o for o in offers if o["first_name"] == "Chris"), None)
    assert all(o.get("sample") is True for o in offers if o is not real)
    r = c.post(f"/v0/match/{offers[0]['match_id']}/decline", headers=me)
    assert r.status_code == 200 and r.json()["status"] == "declined" and r.json()["sample"] is True


def test_a_real_user_cannot_take_the_sample_suffix(c):
    r = c.post("/v0/users", json={"username": "chris_sample", "first_name": "Chris", "timezones": ["America/New_York"]},
               headers={"X-Source-Key": next(iter(os.environ.get("RELAY_SOURCE_KEYS", "k").split(",")))})
    assert r.status_code == 422
