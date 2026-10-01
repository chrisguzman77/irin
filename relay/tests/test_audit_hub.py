"""Audit fixes: the watcher's hub path obeys the app path's checks (invariants 15, 16),
and rate limits are per caller. Reuses test_hub.py's fixtures and helpers."""

import pytest

from test_hub import SRC, c, listing, mongo, pair, store  # noqa: F401

OTHER = {"X-Source-Key": "src-b"}


def other_listing(c, listing_id):
    return listing(c, listing_id, urgency=1, headers=OTHER)


def add_user(key, **kw):
    doc = {"user_id": f"u-{key}", "source_key_hash": store.bearer_hash(key), "cgm_verified": True, "is_demo": True,
           "languages": ["English"], "optins": {"have_buddy": True, "hub_volunteer": True}, **kw}
    store.db()["users"].insert_one(doc)


def test_watcher_without_a_linked_user_sees_only_its_own_buddys_listing(c):
    _, vol = pair(c, "c1" * 16)
    listing(c, "mine")
    assert other_listing(c, "stranger").status_code == 200
    assert [d["listing_id"] for d in c.get("/v0/hub/list", headers=vol).json()] == ["mine"]
    assert c.post("/v0/hub/claim", json={"listing_id": "stranger"}, headers=vol).status_code == 404
    assert c.post("/v0/hub/claim", json={"listing_id": "mine"}, headers=vol).status_code == 200


def test_watcher_with_a_verified_volunteer_user_sees_language_matched_strangers(c):
    _, vol = pair(c, "c2" * 16)
    add_user("src-a")
    add_user("src-b", languages=["english"])
    other_listing(c, "stranger")
    assert [d["listing_id"] for d in c.get("/v0/hub/list", headers=vol).json()] == ["stranger"]
    assert c.post("/v0/hub/claim", json={"listing_id": "stranger"}, headers=vol).status_code == 200


@pytest.mark.parametrize("change", [{"cgm_verified": False}, {"optins": {"have_buddy": True, "hub_volunteer": False}},
                                    {"languages": ["Klingon"]}])
def test_watcher_user_failing_a_rule_sees_no_strangers(c, change):
    _, vol = pair(c, "c3" * 16)
    add_user("src-a", **change)
    add_user("src-b")
    other_listing(c, "stranger")
    assert c.get("/v0/hub/list", headers=vol).json() == []
    assert c.post("/v0/hub/claim", json={"listing_id": "stranger"}, headers=vol).status_code == 404


def test_one_heavy_client_cannot_429_another(c, monkeypatch):
    import relay_api
    monkeypatch.setattr(relay_api, "RATE_LIMIT_PER_MIN", 3)
    relay_api._hits.clear()
    heavy = {"X-Forwarded-For": "1.1.1.1, 10.0.0.1"}
    codes = [c.get("/v0/pair/nope", headers=heavy).status_code for _ in range(5)]
    assert codes[:3] == [404] * 3 and codes[3:] == [429, 429]
    assert c.get("/v0/pair/nope", headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 404  # another IP is untouched
    assert c.get("/v0/hub/audit", headers=SRC).status_code == 200  # the Pi has its own bucket
