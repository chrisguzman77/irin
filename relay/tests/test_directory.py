"""B3+ check: the buddy directory. A candidate asleep during the requester's
night scores zero; a mirror beats a twin with the same hours; declined
candidates never return; both-accept -> accepted; a pair_link reaches the
other device's poll; forbidden fields 422; demo and real never meet;
unverified or opted-out users are never listed or matched; the log holds
ids and kinds only. Zones without DST (Bogota UTC-5, Tokyo UTC+9) keep the
scores independent of the date. Runs against the compose mongo in a
throwaway database."""

import os
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("ATLAS_URI", "mongodb://127.0.0.1:27017")
os.environ["RELAY_SOURCE_KEYS"] = "src-a,src-b"
os.environ["RELAY_DB_NAME"] = f"relay_test_{uuid.uuid4().hex[:8]}"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import relay_api  # noqa: E402
import store  # noqa: E402
from main import app  # noqa: E402

KEYS = [f"dir-{i}" for i in range(8)]
NIGHT_WATCH = [{"weekday": d, "start": "22:00", "end": "08:00"} for d in range(7)]  # awake through a Bogota night
TOKYO_DAY = [{"weekday": d, "start": "12:00", "end": "22:00"} for d in range(7)]  # the same UTC hours, from Tokyo
OFFICE = [{"weekday": d, "start": "09:00", "end": "17:00"} for d in range(7)]


@pytest.fixture(scope="module", autouse=True)
def mongo():
    if store.status() != "ok":
        pytest.skip("no mongo at ATLAS_URI (start deploy/docker-compose.yml's mongo)")
    yield
    store.client().drop_database(store.DB_NAME)


@pytest.fixture
def c(monkeypatch):
    monkeypatch.setattr(relay_api, "RELAY_SOURCE_KEYS", ["src-a", "src-b", *KEYS])  # one source key = one user
    relay_api._hits.clear()
    store.client().drop_database(store.DB_NAME)
    with TestClient(app) as client:
        yield client


def user(c, i, first_name, tz="America/Bogota", availability=(), languages=("en",), watcher=True, have_buddy=True,
         verified=True, is_demo=True, username=None, **extra):
    body = {"username": username or f"{first_name.lower()}{i}", "first_name": first_name, "languages": list(languages),
            "timezones": [tz], "availability": list(availability),
            "optins": {"have_buddy": have_buddy, "be_watcher": watcher, "hub_watchable": False, "hub_volunteer": False},
            "cgm_verified": verified, "is_demo": is_demo, **extra}
    r = c.post("/v0/users", json=body, headers={"X-Source-Key": KEYS[i]})
    assert r.status_code == 200, r.text
    return r.json()["user_id"], {"Authorization": f"Bearer {r.json()['user_bearer']}"}


def offers(c, bearer):
    r = c.post("/v0/match", json={}, headers=bearer)
    assert r.status_code == 200, r.text
    return r.json()


def test_upsert_keeps_the_user_and_the_bearer(c):
    uid, bearer = user(c, 0, "Chris")
    assert user(c, 0, "Chris", languages=("en", "es")) == (uid, bearer)
    assert store.db()["users"].count_documents({}) == 1
    assert c.post("/v0/users", json={}, headers={"X-Source-Key": "nope"}).status_code == 401
    assert c.post("/v0/match", json={}, headers={"Authorization": "Bearer nope"}).status_code == 401
    plain = bearer["Authorization"].split(" ", 1)[1]
    assert plain not in str(list(store.db()["users"].find()))  # only its hash is stored


@pytest.mark.parametrize("extra", [{"glucose": 90}, {"glucose_mgdl": 90}, {"location": "x"}, {"lat": 1}, {"lon": 1},
                                   {"phone": "+14045550123"}, {"email": "a@b.c"}, {"anything_else": 1},
                                   {"optins": {"have_buddy": True, "phone_number": "1"}},
                                   {"availability": [{"weekday": 0, "start": "22:00", "end": "08:00", "location": "x"}]},
                                   {"first_name": "Chris 555"}, {"first_name": "c@x.io"}, {"username": "4045550123"},
                                   {"username": "chris@example.com"}, {"timezones": ["Mars/Base"]}, {"languages": ["en1"]}])
def test_forbidden_fields_422(c, extra):
    body = {"username": "chris", "first_name": "Chris", "languages": ["en"], "timezones": ["America/Bogota"],
            "availability": [], "optins": {}, "cgm_verified": True, "is_demo": True, **extra}
    assert c.post("/v0/users", json=body, headers={"X-Source-Key": KEYS[0]}).status_code == 422
    assert store.db()["users"].count_documents({}) == 0


def test_asleep_scores_zero_and_a_mirror_beats_a_twin(c):
    _, me = user(c, 0, "Chris", languages=("en",))
    asleep, _ = user(c, 1, "Dana", availability=OFFICE, languages=("fr",))
    twin, _ = user(c, 2, "Eli", availability=NIGHT_WATCH, languages=("fr",))
    mirror, _ = user(c, 3, "Mika", tz="Asia/Tokyo", availability=TOKYO_DAY, languages=("fr",))
    got = {o["candidate_id"]: o for o in offers(c, me)}
    assert got[asleep]["score"] == 0 and got[asleep]["hours_covered"] == 0 and got[asleep]["mirror"] is False
    assert got[twin]["hours_covered"] == got[mirror]["hours_covered"] == 10
    assert got[mirror]["mirror"] is True and got[mirror]["score"] == 12 and got[twin]["score"] == 10
    assert [o["candidate_id"] for o in offers(c, me)] == [mirror, twin, asleep]
    assert all(o["status"] == "offered" and o["shared_languages"] == [] for o in got.values())
    assert set(got[mirror]) == {"match_id", "candidate_id", "first_name", "score", "hours_covered", "mirror",
                                "shared_languages", "status"}


def test_top_three_languages_count_and_reoffer_keeps_the_match_id(c):
    _, me = user(c, 0, "Chris", languages=("en", "es"))
    ids = [user(c, i, "Pat", availability=NIGHT_WATCH, languages=("fr",))[0] for i in (1, 2, 3)]
    both, _ = user(c, 4, "Ana", availability=NIGHT_WATCH, languages=("EN", "es"))
    first = offers(c, me)
    assert len(first) == 3 and first[0]["candidate_id"] == both
    assert first[0]["shared_languages"] == ["en", "es"] and first[0]["score"] == 12
    assert [o["match_id"] for o in offers(c, me)] == [o["match_id"] for o in first]
    assert store.db()["matches"].count_documents({}) == 3 and len(ids) == 3


def test_declined_never_return(c):
    me_id, me = user(c, 0, "Chris")
    best, best_bearer = user(c, 1, "Mika", tz="Asia/Tokyo", availability=TOKYO_DAY)
    other, _ = user(c, 2, "Eli", availability=NIGHT_WATCH)
    m = offers(c, me)[0]
    assert m["candidate_id"] == best
    r = c.post(f"/v0/match/{m['match_id']}/decline", headers=me)
    assert r.status_code == 200 and r.json()["status"] == "declined"
    assert [o["candidate_id"] for o in offers(c, me)] == [other]
    assert c.post(f"/v0/match/{m['match_id']}/accept", headers=best_bearer).status_code == 409  # no reviving it
    assert me_id not in [o["candidate_id"] for o in offers(c, best_bearer)]  # never returns from either side


def test_both_accept_then_pair_link_reaches_the_other_poll(c):
    me_id, me = user(c, 0, "Chris")
    them_id, them = user(c, 1, "Mika", tz="Asia/Tokyo", availability=TOKYO_DAY)
    mid = offers(c, me)[0]["match_id"]
    assert c.post(f"/v0/match/{mid}/pair_link", json={"pair_url": "https://watch.x/pair#token=1"},
                  headers={"X-Source-Key": KEYS[0]}).status_code == 409  # not accepted yet
    r = c.post(f"/v0/match/{mid}/accept", headers=me)
    assert r.status_code == 200 and r.json()["status"] == "offered" and r.json()["candidate_id"] == them_id
    r = c.post(f"/v0/match/{mid}/accept", headers=them)
    assert r.status_code == 200 and r.json()["status"] == "accepted" and r.json()["first_name"] == "Chris"
    assert c.post(f"/v0/match/{mid}/decline", headers=them).status_code == 409  # revoke is the pairing revoke
    assert all(o["match_id"] != mid for o in offers(c, me))  # already buddies: not offered again
    url = "https://watch.irin.test/pair#token=" + "ab" * 16 + "&device_pk=x&relay=y"
    r = c.post(f"/v0/match/{mid}/pair_link", json={"pair_url": url}, headers={"X-Source-Key": KEYS[0]})
    assert r.status_code == 200 and r.json() == {"stored": True}
    poll = c.get("/v0/device/irin-b/messages", headers={"X-Source-Key": KEYS[1]}).json()
    assert set(poll) == {"messages", "pairings", "calls", "matches"}
    assert poll["matches"] == [{"match_id": mid, "first_name": "Chris", "status": "accepted", "pair_url": url}]
    mine = c.get("/v0/device/irin-a/messages", headers={"X-Source-Key": KEYS[0]}).json()["matches"]
    assert mine == [{"match_id": mid, "first_name": "Mika", "status": "accepted", "pair_url": None}]
    assert c.post(f"/v0/match/{mid}/pair_link", json={"pair_url": url},
                  headers={"X-Source-Key": KEYS[2]}).status_code == 404  # not a side of this match
    assert c.post(f"/v0/match/{mid}/accept", headers=user(c, 3, "Zed")[1]).status_code == 404
    assert c.get("/v0/device/x/messages", headers={"X-Source-Key": "src-a"}).json()["matches"] == []


def test_demo_and_real_never_meet_and_unverified_or_opted_out_never_listed(c):
    _, demo = user(c, 0, "Chris", is_demo=True, username="chrisd")
    _, real = user(c, 1, "Chris", is_demo=False, username="chrisr", availability=NIGHT_WATCH)
    demo_w, _ = user(c, 2, "Mika", is_demo=True, availability=NIGHT_WATCH, username="mika")
    user(c, 3, "Mila", is_demo=False, verified=False, availability=NIGHT_WATCH, username="mila")
    user(c, 4, "Milo", is_demo=True, watcher=False, have_buddy=False, availability=NIGHT_WATCH, username="milo")
    _, unverified = user(c, 5, "Mira", is_demo=True, verified=False, username="mira")
    assert [o["candidate_id"] for o in offers(c, demo)] == [demo_w]
    assert offers(c, real) == []  # the only real candidate is unverified
    assert [u["user_id"] for u in c.get("/v0/users/search", params={"username": "mi"}, headers=demo).json()] == [demo_w]
    assert c.get("/v0/users/search", params={"username": "mi"}, headers=real).json() == []
    assert c.get("/v0/users/search", params={"username": "chris"}, headers=real).json() == []  # the demo Chris
    assert c.post("/v0/match", json={}, headers=unverified).status_code == 403
    assert c.get("/v0/users/search", params={"username": "mi"}, headers=unverified).status_code == 403
    assert set(c.get("/v0/users/search", params={"username": "mika"}, headers=demo).json()[0]) == \
        {"user_id", "username", "first_name", "languages", "be_watcher"}
    _, nobuddy = user(c, 6, "Noa", have_buddy=False)
    assert c.post("/v0/match", json={}, headers=nobuddy).status_code == 403
    assert c.post("/v0/match", json={"glucose": 1}, headers=demo).status_code == 422


def test_log_holds_ids_and_kinds_only(c):
    _, me = user(c, 0, "Chris")
    _, them = user(c, 1, "Mika", tz="Asia/Tokyo", availability=TOKYO_DAY)
    mid = offers(c, me)[0]["match_id"]
    c.post(f"/v0/match/{mid}/accept", headers=me)
    c.post(f"/v0/match/{mid}/accept", headers=them)
    c.post(f"/v0/match/{mid}/pair_link", json={"pair_url": "https://watch.x/pair#token=secret"},
           headers={"X-Source-Key": KEYS[0]})
    c.get("/v0/users/search", params={"username": "mika"}, headers=me)
    rows = c.get("/v0/log").json()
    routes = {r["route"] for r in rows}
    assert {"directory.user", "directory.match", "directory.accept", "directory.pair_link", "directory.search"} <= routes
    dump = str(rows)
    for secret in ("Chris", "Mika", "mika", "token=secret", "Tokyo", "Bogota", "22:00"):
        assert secret not in dump
