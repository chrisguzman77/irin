"""B3+ on the Pi: the buddy directory profile, matching, accept/decline, and the
poll's `matches` key, against a fake relay (MockTransport), never the network.
Named checks: the profile endpoints are PIN-gated; only cgm_verified reaches
the relay (never the feed URL or token); the intro is written by narrative.py
in a worker thread, validated, with the template as fallback; an accepted
match starts a buddy pairing and posts its qr_url as the pair_link; demo
never creates a real directory entry and never uses the live profile.
Review fixes: the relay's ONE user per source key never leaks a demo match
into live; an expired unused link is minted again; a failing link never
churns the pairing screen; a 401 asks for the profile again; link.mode is
"mirror" for a buddy from a mirror match."""

import asyncio
import json
from datetime import datetime, timedelta

import httpx
import pytest

from app import store
from app.buddy.rung import BuddyRung
from app.clock import clock
from app.contracts import Pairing, Settings
from app.buddy.directory import BuddyDirectory, BuddyProfile, DirectoryError, DirectoryRelayClient
from app.config import config
from app.rounds import narrative
from tests.test_narrative import ROUTING, Fakes

PROFILE = {"username": "lee_t1d", "first_name": "Lee", "languages": ["English", "Spanish"],
           "timezones": ["America/New_York"], "availability": [{"weekday": 0, "start": "22:00", "end": "23:59"}],
           "optins": {"have_buddy": True, "be_watcher": True, "hub_watchable": False, "hub_volunteer": False}}
OFFERS = [{"match_id": "m1", "candidate_id": "u2", "first_name": "Sam", "score": 11, "hours_covered": 8,
           "mirror": True, "shared_languages": ["English"], "status": "offered"},
          {"match_id": "m2", "candidate_id": "u3", "first_name": "Ana", "score": 7, "hours_covered": 6,
           "mirror": False, "shared_languages": ["English", "Spanish"], "status": "offered"}]
HUB = [{"listing_id": "h1", "first_name": "Kai", "languages": ["English"], "elapsed_min": 7, "urgency": 2,
        "confidence": "device_confirmed", "sample": True}]


class FakeRelay:
    """The relay's directory, pairing, and poll routes; records every request."""

    def __init__(self):
        self.requests: list[tuple[str, str, dict, dict]] = []  # method, path, body, headers
        self.match_status = {"m1": "offered", "m2": "offered"}
        self.poll_matches: list[dict] | None = None
        self.users_status = 200
        self.match_forbidden = False
        self.pair_link_status = None  # force an answer from pair_link (e.g. 404)
        self.bearer_dead = False  # a relay DB reset: every bearer call is 401
        self.user_is_demo = None  # ONE user per source key: the last upsert's is_demo wins
        self.hub_claimed: set[str] = set()  # buddy v3: listings someone holds


    def transport(self):
        def handler(request: httpx.Request) -> httpx.Response:
            path, method = request.url.path, request.method
            body = json.loads(request.content) if request.content else {}
            self.requests.append((method, path, body, dict(request.headers)))
            if path == "/v0/users":
                if self.users_status != 200:
                    return httpx.Response(self.users_status, json={"detail": "no"})
                self.user_is_demo = body.get("is_demo")
                return httpx.Response(200, json={"user_id": "u1", "user_bearer": "bearer-secret"})
            if self.bearer_dead and request.headers.get("authorization"):
                return httpx.Response(401, json={"detail": "unknown bearer"})
            if path == "/v0/match":
                if self.match_forbidden:  # unverified, or have_buddy off
                    return httpx.Response(403, json={"detail": "not eligible"})
                return httpx.Response(200, json=OFFERS)
            if path.startswith("/v0/match/") and path.endswith("/pair_link"):
                if self.pair_link_status:
                    return httpx.Response(self.pair_link_status, json={"detail": "forced"})
                if self.match_status.get(path.split("/")[3]) != "accepted":
                    return httpx.Response(409, json={"detail": "not accepted by both"})
                return httpx.Response(200, json={"stored": True})
            if path.startswith("/v0/match/"):
                _, _, _, mid, action = path.split("/")
                if mid not in self.match_status:
                    return httpx.Response(404)
                current = self.match_status[mid]
                if (action, current) in (("accept", "declined"), ("decline", "accepted")):
                    return httpx.Response(409, json={"detail": "conflict"})
                if action == "decline":
                    self.match_status[mid] = "declined"
                return httpx.Response(200, json={"match_id": mid, "status": self.match_status[mid]})
            if path == "/v0/users/hub":
                return httpx.Response(200, json=HUB)
            if path.startswith("/v0/users/hub/") and path.endswith("/claim"):
                lid = path.split("/")[4]
                if lid not in {h["listing_id"] for h in HUB}:
                    return httpx.Response(404, json={"detail": "no such listing"})
                if lid in self.hub_claimed:
                    return httpx.Response(409, json={"detail": "someone else is helping Kai",
                                                     "holder_expires_at": "2020-01-01T21:03:00+00:00"})
                self.hub_claimed.add(lid)
                return httpx.Response(200, json={"claim_id": "c1", "expires_at": "2020-01-01T21:03:00+00:00",
                                                 "script": {"steps": ["Call Kai", "Ask if they ate sugar"]}})
            if path == "/v0/pair":
                return httpx.Response(200, json={"ok": True})
            if path.endswith("/messages"):
                out = {"messages": [], "pairings": [], "calls": []}
                if self.poll_matches is not None:
                    out["matches"] = self.poll_matches
                return httpx.Response(200, json=out)
            return httpx.Response(404)

        return httpx.MockTransport(handler)

    def to(self, suffix):
        return [r for r in self.requests if r[1].endswith(suffix)]


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(store.config, "IRIN_DB", str(tmp_path / "t.db"))
    store.init_db()
    clock.set(speed=1.0, start=T0)
    yield
    clock.reset()


T0 = datetime(2020, 1, 1, 21, 0)


def make_dir(relay, demo=True, verified=True, links=None, updates=None):
    world = {"demo": demo}

    async def verify():
        return verified

    def start_pairing():
        n = len(links) if links is not None else 0
        if links is not None:
            links.append(n)
        return {"token": f"t{n}", "qr_url": f"https://watch.test/pair#token=t{n}&device_pk=pk&relay=https://relay.test",
                "expires_at": (clock.now() + timedelta(minutes=10)).isoformat()}

    d = BuddyDirectory(relay_url="http://relay.test", source_key="k", is_demo=lambda: world["demo"],
                       verify_cgm=verify, start_pairing=start_pairing,
                       on_update=(updates.append if updates is not None else None), transport=relay.transport())
    return d, world


def run(coro):
    return asyncio.run(coro)


# --- only cgm_verified reaches the relay ---


def test_live_profile_sends_cgm_verified_only_never_the_feed(db, monkeypatch):
    monkeypatch.setattr(config, "NIGHTSCOUT_URL", "https://feed.example.test")
    monkeypatch.setattr(config, "NIGHTSCOUT_TOKEN", "feed-token-secret")
    relay = FakeRelay()
    d, _ = make_dir(relay, demo=False, verified=True)
    out = run(d.save_profile(BuddyProfile(**PROFILE)))
    assert out.cgm_verified is True and out.user_id == "u1" and out.is_demo is False
    [(_, _, body, headers)] = relay.to("/v0/users")
    assert set(body) == set(PROFILE) | {"cgm_verified", "is_demo"}
    assert body["cgm_verified"] is True and body["is_demo"] is False and headers["x-source-key"] == "k"
    raw = json.dumps(body)
    assert "feed.example.test" not in raw and "feed-token-secret" not in raw and "nightscout" not in raw.lower()
    assert "bearer-secret" not in out.model_dump_json()  # the bearer stays on the device
    assert json.loads(store.get_kv("buddy_user:live"))["user_bearer"] == "bearer-secret"


def test_an_unverified_live_feed_is_sent_as_false(db):
    relay = FakeRelay()
    d, _ = make_dir(relay, demo=False, verified=False)
    assert run(d.save_profile(BuddyProfile(**PROFILE))).cgm_verified is False
    assert relay.to("/v0/users")[0][2]["cgm_verified"] is False


def test_the_live_feed_check_needs_a_recent_reading(db, monkeypatch):
    from app import main
    from app.datasource.nightscout import NightscoutDataSource

    async def fresh():
        return [{"date": int(datetime.now().timestamp() * 1000), "sgv": 110, "direction": "Flat"}]

    async def down():
        raise httpx.ConnectError("down")

    monkeypatch.setattr(main.runtime, "datasource", NightscoutDataSource("https://feed.test", "t", fetch=fresh))
    assert run(main._cgm_feed_verified()) is True
    monkeypatch.setattr(main.runtime, "datasource", NightscoutDataSource("https://feed.test", "t", fetch=down))
    assert run(main._cgm_feed_verified()) is False
    monkeypatch.setattr(main.runtime, "datasource", main.make_datasource("replay"))
    assert run(main._cgm_feed_verified()) is False  # replay is never a live feed


@pytest.mark.parametrize("field,value", [("username", "me@example.com"), ("username", "5551234567"),
                                         ("username", "_lee"),
                                         ("first_name", "Lee 555"), ("timezones", ["not a zone"]),
                                         ("availability", [{"weekday": 7, "start": "22:00", "end": "23:00"}])])
def test_profile_fields_refuse_contact_details_and_bad_shapes(field, value):
    with pytest.raises(ValueError):
        BuddyProfile(**{**PROFILE, field: value})


def test_a_relay_422_is_a_422_and_an_unreachable_relay_a_502(db):
    relay = FakeRelay()
    relay.users_status = 422
    d, _ = make_dir(relay)
    with pytest.raises(DirectoryError) as e:
        run(d.save_profile(BuddyProfile(**PROFILE)))
    assert e.value.status == 422

    def boom(request):
        raise httpx.ConnectError("down")

    d.transport = httpx.MockTransport(boom)
    with pytest.raises(DirectoryError) as e:
        run(d.save_profile(BuddyProfile(**PROFILE)))
    assert e.value.status == 502 and store.get_kv("buddy_user:demo") is None


# --- demo isolation ---


def test_demo_never_creates_a_real_entry_and_never_uses_the_live_profile(db):
    relay = FakeRelay()
    d, world = make_dir(relay, demo=True, verified=False)  # demo skips the feed check: true
    out = run(d.save_profile(BuddyProfile(**PROFILE)))
    assert out.cgm_verified is True and out.is_demo is True and out.profile.is_demo is True
    assert relay.to("/v0/users")[0][2]["is_demo"] is True
    world["demo"] = False
    assert d.profile() is None and store.get_kv("buddy_user:live") is None
    with pytest.raises(DirectoryError) as e:
        run(d.find_matches())  # the demo bearer is never used for a live match
    assert e.value.status == 409 and not relay.to("/v0/match")
    world["demo"] = True
    assert d.profile().first_name == "Lee"


# --- the intro: narrative in a worker thread, validated, template fallback ---


def test_match_offers_carry_a_template_intro_and_a_computed_why(db):
    relay = FakeRelay()
    d, _ = make_dir(relay)
    run(d.save_profile(BuddyProfile(**PROFILE)))
    offers = run(d.find_matches())
    assert [o.match_id for o in offers] == ["m1", "m2"]
    sam = offers[0]
    assert sam.intro == narrative._buddy_intro_template({"name": "Sam", "shared_languages": ["English"]},
                                                        {"hours_covered": 8})
    assert sam.why == "Awake for 8 of your night hours; a mirror across time zones: their day is your night; " \
                      "you share English."
    assert set(sam.model_dump()) == {"match_id", "first_name", "hours_covered", "mirror", "shared_languages",
                                     "score", "intro", "why", "is_demo", "sample"}
    [(_, _, _, headers)] = relay.to("/v0/match")
    assert headers["authorization"] == "Bearer bearer-secret"
    assert [m["status"] for m in d.snapshot()] == ["offered", "offered"]


@pytest.fixture
def muse(monkeypatch):
    monkeypatch.setattr(config, "NARRATIVE_BACKEND", "backboard")
    monkeypatch.setattr(config, "NARRATIVE_ROUTING", ROUTING)


def test_a_valid_muse_intro_ships_and_it_ran_off_the_event_loop(db, monkeypatch, muse):
    fakes = Fakes(monkeypatch, meta="Sam is awake for 8 of your night hours and speaks English too.")
    relay = FakeRelay()
    d, _ = make_dir(relay)
    run(d.save_profile(BuddyProfile(**PROFILE)))
    offers = run(d.find_matches())
    assert offers[0].intro == "Sam is awake for 8 of your night hours and speaks English too."
    assert "meta" in fakes.names()  # on the event loop generate() would have returned the template uncalled
    prompt = fakes.calls[0][1]
    assert "Sam" in prompt and "mgdl" not in prompt.lower() and "glucose" not in prompt.lower()


def test_an_intro_with_an_invented_number_falls_back_to_the_template(db, monkeypatch, muse):
    Fakes(monkeypatch, meta="Sam covers 11 of your night hours.")  # 11 is the score, not hours_covered
    relay = FakeRelay()
    d, _ = make_dir(relay)
    run(d.save_profile(BuddyProfile(**PROFILE)))
    sam = run(d.find_matches())[0]
    assert sam.intro == narrative._buddy_intro_template({"name": "Sam", "shared_languages": ["English"]},
                                                        {"hours_covered": 8})


# --- accept / decline: the buddy pairing and the pair_link ---


def test_accept_by_both_starts_a_buddy_pairing_and_posts_the_pair_link_once(db):
    relay = FakeRelay()
    links, updates = [], []
    d, _ = make_dir(relay, links=links, updates=updates)
    run(d.save_profile(BuddyProfile(**PROFILE)))
    run(d.find_matches())
    out = run(d.respond("m1", "accept"))
    assert out.status == "offered" and links == [] and not relay.to("/pair_link")  # the other side has not accepted
    relay.match_status["m1"] = "accepted"
    assert run(d.respond("m1", "accept")).status == "accepted"
    assert links == [0]
    [(_, path, body, headers)] = relay.to("/pair_link")
    assert path == "/v0/match/m1/pair_link" and headers["x-source-key"] == "k"
    assert body == {"pair_url": "https://watch.test/pair#token=t0&device_pk=pk&relay=https://relay.test"}
    run(d.on_matches([{"match_id": "m1", "first_name": "Sam", "status": "accepted", "pair_url": "https://w/p#x"}]))
    assert links == [0] and len(relay.to("/pair_link")) == 1  # never a second pairing for the same match
    assert updates[-1] == {"event": "match", "match_id": "m1", "status": "accepted", "pair_url": "https://w/p#x",
                           "sample": False}


def test_an_accept_seen_first_on_the_poll_starts_the_pairing_and_a_failure_retries(db):
    relay = FakeRelay()
    links = []
    d, _ = make_dir(relay, links=links)
    fail = {"on": True}
    good = d.start_pairing

    def flaky():
        if fail["on"]:
            raise RuntimeError("relay refused the pairing token")
        return good()

    d.start_pairing = flaky
    run(d.save_profile(BuddyProfile(**PROFILE)))
    relay.match_status["m2"] = "accepted"
    row = {"match_id": "m2", "first_name": "Ana", "status": "accepted", "pair_url": None}
    run(d.on_matches([row]))
    assert not relay.to("/pair_link")
    fail["on"] = False
    run(d.on_matches([row]))
    assert len(relay.to("/pair_link")) == 1 and links == [0]


def test_decline_returns_the_status_and_never_pairs(db):
    relay = FakeRelay()
    links = []
    d, _ = make_dir(relay, links=links)
    run(d.save_profile(BuddyProfile(**PROFILE)))
    out = run(d.respond("m2", "decline"))
    assert (out.match_id, out.status) == ("m2", "declined") and links == []
    with pytest.raises(DirectoryError) as e:
        run(d.respond("nope", "accept"))
    assert e.value.status == 404


def test_relay_refusals_reach_the_app_as_clear_reasons(db):
    relay = FakeRelay()
    d, _ = make_dir(relay)
    run(d.save_profile(BuddyProfile(**PROFILE)))
    relay.match_forbidden = True
    with pytest.raises(DirectoryError) as e:
        run(d.find_matches())
    assert e.value.status == 403 and "verified" in e.value.detail and "have a buddy" in e.value.detail
    run(d.respond("m2", "decline"))
    with pytest.raises(DirectoryError) as e:
        run(d.respond("m2", "accept"))
    assert e.value.status == 409 and "declined" in e.value.detail
    relay.match_status["m1"] = "accepted"
    with pytest.raises(DirectoryError) as e:
        run(d.respond("m1", "decline"))
    assert e.value.status == 409 and "revok" in e.value.detail


def test_fix3_a_pair_link_the_relay_refuses_is_attempted_once(db):
    relay = FakeRelay()
    links = []
    d, _ = make_dir(relay, links=links)
    run(d.save_profile(BuddyProfile(**PROFILE)))
    relay.match_status["m1"] = "accepted"
    for code in (409, 404):
        relay.requests.clear()
        relay.pair_link_status = code
        mid = "m1" if code == 409 else "m2"
        row = {"match_id": mid, "first_name": "Sam", "status": "accepted", "pair_url": None}
        for _ in range(3):  # three polls
            run(d.on_matches([row]))
        assert len(relay.to("/pair_link")) == 1, code
    assert links == [0, 1]  # one pairing per match, never one per poll


def test_fix3_a_pending_doctor_pairing_is_never_replaced_and_a_live_buddy_token_is_reused(db):
    relay = FakeRelay()
    links = []
    d, _ = make_dir(relay, links=links)
    run(d.save_profile(BuddyProfile(**PROFILE)))
    relay.match_status["m1"] = "accepted"
    d.pending = lambda: ("doctor", "doc-token")  # a doctor pairing is on screen
    row = {"match_id": "m1", "first_name": "Sam", "status": "accepted", "pair_url": None}
    run(d.on_matches([row]))
    run(d.respond("m1", "accept"))
    assert links == [] and not relay.to("/pair_link")
    d.pending = lambda: None  # the doctor pairing finished
    relay.pair_link_status = 502  # our link is minted, the post fails
    run(d.on_matches([row]))
    assert links == [0] and len(relay.to("/pair_link")) == 1
    d.pending = lambda: ("buddy", "t0")  # our own token is still live: reused, never a new one
    relay.pair_link_status = None
    run(d.on_matches([row]))
    assert links == [0] and len(relay.to("/pair_link")) == 2
    assert relay.to("/pair_link")[-1][2]["pair_url"].startswith("https://watch.test/pair#token=t0&")


def test_fix2_an_expired_unused_link_is_minted_again_on_the_poll(db):
    relay = FakeRelay()
    links = []
    d, _ = make_dir(relay, links=links)
    run(d.save_profile(BuddyProfile(**PROFILE)))
    relay.match_status["m1"] = "accepted"
    run(d.respond("m1", "accept"))
    row = {"match_id": "m1", "first_name": "Sam", "status": "accepted", "pair_url": "https://w/p#x"}
    run(d.on_matches([row]))
    assert links == [0] and len(relay.to("/pair_link")) == 1  # still live: no new link
    clock.advance(11 * 60)
    run(d.on_matches([row]))
    assert links == [0, 1] and len(relay.to("/pair_link")) == 2
    assert relay.to("/pair_link")[-1][2]["pair_url"].startswith("https://watch.test/pair#token=t1&")
    # the second link is used: a buddy pairing confirmed while it was live, so never a third
    used = [Pairing(device_id="irin", doctor_id="b1", doctor_display_name="Sam", doctor_pk="pk",
                    status="paired", confirmed_at=clock.now(), peer_kind="buddy", is_demo=True)]
    d.buddy_pairings = lambda: used
    clock.advance(11 * 60)
    run(d.on_matches([row]))
    assert links == [0, 1] and len(relay.to("/pair_link")) == 2


def test_fix1_the_single_relay_user_never_leaks_a_demo_match_into_live(db):
    relay = FakeRelay()
    links = []
    d, world = make_dir(relay, demo=False, links=links)
    run(d.save_profile(BuddyProfile(**PROFILE)))  # live first: a live bearer exists
    world["demo"] = True
    run(d.save_profile(BuddyProfile(**PROFILE)))  # the relay's one user flips to demo
    assert relay.user_is_demo is True
    world["demo"] = False  # back to live: the live profile and bearer are still on the device
    assert d.profile() is not None
    relay.requests.clear()
    for call in (d.find_matches(), d.respond("m1", "accept")):
        with pytest.raises(DirectoryError) as e:
            run(call)
        assert e.value.status == 409 and e.value.detail == "save your buddy profile in this mode first"
    relay.match_status["m1"] = "accepted"  # the demo match was accepted in replay
    run(d.on_matches([{"match_id": "m1", "first_name": "Sam", "status": "accepted", "pair_url": "https://w/p#x"}]))
    assert d.snapshot() == [] and links == [] and relay.requests == []  # ignored: no pairing, no relay call
    run(d.save_profile(BuddyProfile(**PROFILE)))  # the live profile saved again: live owns the user
    run(d.on_matches([{"match_id": "m9", "first_name": "Ana", "status": "offered", "pair_url": None}]))
    assert [m["match_id"] for m in d.snapshot()] == ["m9"]


def test_fix4_a_401_asks_for_the_profile_again_and_forgets_the_bearer(db):
    relay = FakeRelay()
    d, _ = make_dir(relay)
    run(d.save_profile(BuddyProfile(**PROFILE)))
    relay.bearer_dead = True
    with pytest.raises(DirectoryError) as e:
        run(d.find_matches())
    assert (e.value.status, e.value.detail) == (409, "save your buddy profile again")
    assert not store.get_kv("buddy_user:demo")
    with pytest.raises(DirectoryError) as e:
        run(d.find_matches())
    assert e.value.status == 409 and len(relay.to("/v0/match")) == 1  # the stale bearer is never sent again
    relay.bearer_dead = False
    run(d.save_profile(BuddyProfile(**PROFILE)))
    assert len(run(d.find_matches())) == 2


def test_fix5_link_mode_is_mirror_for_a_buddy_from_a_mirror_match_and_twin_otherwise(db):
    relay = FakeRelay()
    d, _ = make_dir(relay)
    run(d.save_profile(BuddyProfile(**PROFILE)))
    run(d.find_matches())  # m1 mirror, m2 not
    relay.match_status["m1"] = "accepted"
    run(d.respond("m1", "accept"))

    def buddy(confirmed_at):
        return Pairing(device_id="irin", doctor_id="b1", doctor_display_name="Sam", doctor_pk="pk",
                       status="paired", confirmed_at=confirmed_at, peer_kind="buddy", is_demo=True)

    paired = buddy(clock.now() + timedelta(minutes=2))  # confirmed while m1's link was live
    rung = BuddyRung(settings=Settings(), alarm_state=lambda: None, recorder=None,
                     recipients=lambda demo: [paired], post_card=None, post_hub=None, spawn=lambda c: None,
                     device_id="irin", is_demo=lambda: True, link_mode=d.mode_for)
    assert rung.link() == {"first_name": "Sam", "mode": "mirror", "peer_id": "b1"}
    paired = buddy(T0 - timedelta(days=3))  # a pre-matched demo pair, no match behind it
    assert rung.link()["mode"] == "twin"
    rung.link_mode = lambda p: 1 / 0  # a failing directory never breaks the snapshot
    assert rung.link()["mode"] == "twin"


# --- the poll ---


def test_the_poll_matches_key_reaches_the_directory_and_other_keys_are_unchanged(db):
    relay = FakeRelay()
    updates = []
    d, _ = make_dir(relay, updates=updates)
    run(d.save_profile(BuddyProfile(**PROFILE)))
    relay.poll_matches = [{"match_id": "m1", "first_name": "Sam", "status": "offered", "pair_url": None}]
    client = DirectoryRelayClient(relay_url="http://relay.test", source_key="k", device_id="irin-test",
                                  transport=relay.transport(), on_matches=d.on_matches)
    body = run(client.poll())
    assert set(body) == {"messages", "pairings", "calls", "matches"}
    assert d.snapshot() == [{"match_id": "m1", "first_name": "Sam", "status": "offered", "pair_url": None,
                             "sample": False}]
    assert updates == [{"event": "match", "match_id": "m1", "status": "offered", "pair_url": None, "sample": False}]
    run(client.poll())
    assert len(updates) == 1  # an unchanged match is not re-broadcast
    relay.poll_matches = []
    run(client.poll())
    assert d.snapshot() == []


# --- the endpoints ---


@pytest.fixture
def app_client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from app import auth, main

    monkeypatch.setattr(auth.config, "PIN", "1234")
    relay = FakeRelay()
    sent = []

    async def capture(msg):
        sent.append(msg)

    with TestClient(main.app) as c:
        monkeypatch.setattr(main.hub, "broadcast", capture)
        d = main.runtime.directory
        monkeypatch.setattr(d, "transport", relay.transport())
        monkeypatch.setattr(d, "relay_url", "http://relay.test")
        monkeypatch.setattr(d, "source_key", "k")
        p = main.runtime.pairing
        monkeypatch.setattr(p.relay, "transport", relay.transport())
        monkeypatch.setattr(p, "watch_url", "https://watch.test")
        yield c, main, relay, sent
        d.reset()
        p.pending = None


PIN = {"X-PIN": "1234"}


def test_every_directory_endpoint_is_pin_gated(app_client):
    c, *_ = app_client
    for method, path in [("get", "/api/buddy/profile"), ("post", "/api/buddy/profile"), ("post", "/api/buddy/match"),
                         ("post", "/api/buddy/match/m1/accept"), ("post", "/api/buddy/match/m1/decline")]:
        kw = {"json": PROFILE} if path.endswith("profile") and method == "post" else {}
        assert getattr(c, method)(path, **kw).status_code == 401, path
        assert getattr(c, method)(path, headers={"X-PIN": "0000"}, **kw).status_code == 401, path


def test_endpoints_end_to_end_in_demo(app_client):
    c, main, relay, sent = app_client
    assert main.runtime.mode == "replay"
    assert c.get("/api/buddy/profile", headers=PIN).json() is None
    assert c.post("/api/buddy/match", headers=PIN).status_code == 409  # no profile yet
    r = c.post("/api/buddy/profile", headers=PIN, json=PROFILE)
    assert r.status_code == 200
    assert r.json() == {"profile": {**PROFILE, "is_demo": True}, "cgm_verified": True, "user_id": "u1", "is_demo": True}
    assert relay.to("/v0/users")[0][2]["is_demo"] is True
    assert c.get("/api/buddy/profile", headers=PIN).json()["username"] == "lee_t1d"
    assert c.post("/api/buddy/profile", headers=PIN, json={**PROFILE, "username": "a@b.c"}).status_code == 422

    offers = c.post("/api/buddy/match", headers=PIN).json()
    assert [o["match_id"] for o in offers] == ["m1", "m2"] and all(o["is_demo"] for o in offers)
    assert "candidate_id" not in offers[0]

    relay.match_status["m1"] = "accepted"
    r = c.post("/api/buddy/match/m1/accept", headers=PIN)
    assert r.json() == {"match_id": "m1", "status": "accepted", "is_demo": True}
    [(_, _, pair, _)] = relay.to("/v0/pair")
    assert pair["peer_kind"] == "buddy" and pair["is_demo"] is True  # a demo pairing: demo alerts only
    [(_, _, link, _)] = relay.to("/pair_link")
    assert link["pair_url"].startswith("https://watch.test/pair#token=" + pair["token"])
    assert main.runtime.pairing.state()["peer_kind"] == "buddy"
    assert c.post("/api/buddy/match/m2/decline", headers=PIN).json()["status"] == "declined"

    hub_updates = [m.payload for m in sent if m.type == "hub_update"]
    assert {"event": "match", "match_id": "m1", "status": "accepted", "pair_url": None, "sample": False} in hub_updates
    with c.websocket_connect("/ws") as ws:
        snap = json.loads(ws.receive_text())
    matches = snap["payload"]["buddy_state"]["matches"]
    assert {m["match_id"]: m["status"] for m in matches} == {"m1": "accepted", "m2": "declined"}
    assert set(matches[0]) == {"match_id", "first_name", "status", "pair_url", "sample"}


def test_a_mode_switch_hides_the_other_worlds_profile_and_matches(app_client):
    c, main, relay, _ = app_client
    c.post("/api/buddy/profile", headers=PIN, json=PROFILE)
    c.post("/api/buddy/match", headers=PIN)
    assert main.runtime.directory.snapshot()
    try:
        assert c.post("/api/mode", json={"mode": "nightscout"}, headers=PIN).status_code == 200
        assert main.runtime.directory.snapshot() == []
        assert c.get("/api/buddy/profile", headers=PIN).json() is None
        assert c.post("/api/buddy/match", headers=PIN).status_code == 409  # the demo bearer is never used live
    finally:
        c.post("/api/mode", json={"mode": "replay"}, headers=PIN)
    assert c.get("/api/buddy/profile", headers=PIN).json()["is_demo"] is True


def test_v2_sample_flag_passes_through_poll_rows_and_the_hub_update(db):
    """Onboarding v2: a seeded sample profile is never shown as a real person."""
    import asyncio
    from app.buddy.directory import BuddyDirectory
    ups = []
    d = BuddyDirectory(relay_url="", source_key="", is_demo=lambda: False, verify_cgm=lambda: None,
                       start_pairing=lambda: {}, on_update=ups.append)
    store.set_kv("buddy_relay_world", "live")
    asyncio.run(d.on_matches([{"match_id": "m1", "first_name": "Sam", "status": "offered", "pair_url": None,
                               "sample": True},
                              {"match_id": "m2", "first_name": "Ana", "status": "offered", "pair_url": None}]))
    rows = {r["match_id"]: r for r in d.snapshot()}
    assert rows["m1"]["sample"] is True and rows["m2"]["sample"] is False
    assert any(u["match_id"] == "m1" and u["sample"] is True for u in ups)


# --- buddy v3: my_buddy, the why line, the hub proxy ---


def test_v3_every_offer_is_stored_and_my_buddy_is_the_accepted_one(db):
    relay = FakeRelay()
    d, world = make_dir(relay)
    run(d.save_profile(BuddyProfile(**PROFILE)))
    offers = run(d.find_matches())
    assert json.loads(store.get_kv("buddy_offer:demo:m2")) == offers[1].model_dump()
    assert d.my_buddy() is None  # offered only
    relay.match_status["m2"] = "accepted"
    run(d.respond("m2", "accept"))
    assert d.my_buddy() == offers[1].model_dump()
    world["demo"] = False  # the other world never sees this world's buddy
    d.reset()
    assert d.my_buddy() is None


def test_v3_my_buddy_is_null_after_a_decline(db):
    relay = FakeRelay()
    d, _ = make_dir(relay)
    run(d.save_profile(BuddyProfile(**PROFILE)))
    run(d.find_matches())
    run(d.respond("m1", "decline"))
    assert d.my_buddy() is None


def test_v3_the_why_line_is_written_by_match_why_and_falls_back_on_an_invented_number(db, monkeypatch, muse):
    fakes = Fakes(monkeypatch, meta="Sam covers 11 of your night hours.")  # 11 is the score, not hours_covered
    relay = FakeRelay()
    d, _ = make_dir(relay)
    run(d.save_profile(BuddyProfile(**PROFILE)))
    sam = run(d.find_matches())[0]
    assert sam.why == "Awake for 8 of your night hours; a mirror across time zones: their day is your night; " \
                      "you share English."
    assert any("shared_language_count" in c[1] for c in fakes.calls)  # match_why reached Muse, then fell back


def test_v3_hub_endpoints_proxy_with_the_user_bearer(app_client):
    c, main, relay, sent = app_client
    for method, path in [("get", "/api/buddy/hub"), ("post", "/api/buddy/hub/h1/claim")]:
        assert getattr(c, method)(path).status_code == 401, path
    assert c.get("/api/buddy/hub", headers=PIN).status_code == 409  # no profile yet
    c.post("/api/buddy/profile", headers=PIN, json=PROFILE)
    assert c.get("/api/buddy/hub", headers=PIN).json() == HUB
    [(_, _, _, headers)] = relay.to("/v0/users/hub")
    assert headers["authorization"] == "Bearer bearer-secret"
    r = c.post("/api/buddy/hub/h1/claim", headers=PIN)
    assert r.status_code == 200 and r.json()["script"] == {"steps": ["Call Kai", "Ask if they ate sugar"]}
    r = c.post("/api/buddy/hub/h1/claim", headers=PIN)
    assert (r.status_code, r.json()["detail"]) == (409, "someone else is helping Kai")  # the relay's words
    r = c.post("/api/buddy/hub/nope/claim", headers=PIN)
    assert (r.status_code, r.json()["detail"]) == (404, "no such listing")

    with c.websocket_connect("/ws") as ws:
        assert json.loads(ws.receive_text())["payload"]["buddy_state"]["my_buddy"] is None
    c.post("/api/buddy/match", headers=PIN)
    relay.match_status["m1"] = "accepted"
    c.post("/api/buddy/match/m1/accept", headers=PIN)
    with c.websocket_connect("/ws") as ws:
        mine = json.loads(ws.receive_text())["payload"]["buddy_state"]["my_buddy"]
    assert mine["match_id"] == "m1" and mine["first_name"] == "Sam" and mine["is_demo"] is True


def test_v3_the_hub_answers_409_in_the_other_world_and_a_401_asks_for_the_profile(app_client):
    c, main, relay, _ = app_client
    c.post("/api/buddy/profile", headers=PIN, json=PROFILE)
    try:
        assert c.post("/api/mode", json={"mode": "nightscout"}, headers=PIN).status_code == 200
        r = c.get("/api/buddy/hub", headers=PIN)
        assert (r.status_code, r.json()["detail"]) == (409, "save your buddy profile in this mode first")
        assert c.post("/api/buddy/hub/h1/claim", headers=PIN).status_code == 409
    finally:
        c.post("/api/mode", json={"mode": "replay"}, headers=PIN)
    assert not relay.to("/v0/users/hub") and not relay.to("/claim")
    relay.bearer_dead = True
    r = c.get("/api/buddy/hub", headers=PIN)
    assert (r.status_code, r.json()["detail"]) == (409, "save your buddy profile again")
