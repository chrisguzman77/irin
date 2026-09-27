"""B2 + B4 named test: full alarm, presence home, unacknowledged past T+10 ->
ONE buddy alert sealed to the paired buddy, carrying no glucose value; never
fires when the room is empty or the radar unknown; brain-only fires only on a
confirmed level 2 low and labels unconfirmed; a claim, a call, or treating
leaves the T+20 emergency clock untouched and it fires at T+20; recovery and
ack resolve; the treating endpoint is PIN-gated, 409 without an alert, and
broadcasts treating_set; a brokered call plays the non-alarm chime and never
stops an alarm; demo alerts never reach a non-demo pairing; the rung only
observes alarm.py. No network, no sleep: clock.advance and a MockTransport."""

import asyncio
import inspect
import json
import time
from datetime import datetime

import httpx
import pytest
from nacl.public import Box, PrivateKey, PublicKey

from app import store
from app.alarm import AlarmEngine
from app.buddy import rung as rung_mod
from app.buddy import treating as treating_mod
from app.buddy.rung import EMERGENCY_TEXT, BuddyRung
from app.buddy.treating import BUDDY_CHIME, TreatingError, handle_calls, set_treating
from app.clock import clock
from app.contracts import EmergencyScript, Pairing, Reading, Settings
from app.rounds import crypto
from app.rounds.alarm_events import AlarmEventRecorder
from app.rounds.relay_client import RelayClient
from hardware.hal import SOUND_NAMES
from hardware.mock import MockHAL

T0 = datetime(2020, 1, 1, 3, 0)


class FakeRelay:
    """The relay and Irin Cloud behind one MockTransport (the cloud answers
    audio_url None, VOICE_BACKEND=none, unless a test sets audio_url)."""

    def __init__(self):
        self.cards: list[dict] = []
        self.hub: list[tuple[str, dict]] = []
        self.calls: list[dict] = []
        self.renders: list[tuple[dict, dict]] = []
        self.notifies: list[tuple[dict, dict]] = []
        self.audio_url: str | None = None

    def transport(self):
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == "/v1/audio/render":
                self.renders.append((json.loads(request.content), dict(request.headers)))
                return httpx.Response(200, json={"audio_url": self.audio_url})
            if path == "/v0/buddy/notify":
                self.notifies.append((json.loads(request.content), dict(request.headers)))
                return httpx.Response(200, json={"sent": True, "reason": "ok"})
            if path == "/v0/cards":
                self.cards.append(json.loads(request.content))
                return httpx.Response(200, json={"stored": True})
            if path.startswith("/v0/hub/"):
                self.hub.append((path.rsplit("/", 1)[1], json.loads(request.content)))
                return httpx.Response(200, json={"ok": True})
            if path.endswith("/messages"):
                return httpx.Response(200, json={"messages": [], "pairings": [], "calls": self.calls})
            return httpx.Response(404)

        return httpx.MockTransport(handler)


class Rig:
    def __init__(self, tmp_path, monkeypatch, demo=True):
        monkeypatch.setattr(store.config, "IRIN_DB", str(tmp_path / "t.db"))
        store.init_db()
        monkeypatch.setattr(crypto, "KEYS_DIR", tmp_path / "keys")
        clock.set(speed=60.0, start=T0)
        self.brain = False
        self.demo = demo
        self.hal = MockHAL()
        self.eng = AlarmEngine(Settings(), hal=self.hal)
        self.rec = AlarmEventRecorder(is_demo=lambda: self.demo, brain_only=lambda: self.brain)
        self.eng.on_transition(self.rec)
        self.relay = FakeRelay()
        self.client = RelayClient(relay_url="http://relay.test", source_key="k", device_id="irin-test",
                                  transport=self.relay.transport())
        self.buddy_sk, buddy_pk = crypto.generate_keypair()
        _, real_pk = crypto.generate_keypair()
        _, doc_pk = crypto.generate_keypair()
        self.pairs = {
            "buddy-demo": Pairing(device_id="irin-test", doctor_id="buddy-demo", doctor_display_name="Sam", doctor_pk=buddy_pk,
                                  status="paired", peer_kind="buddy", is_demo=True),
            "buddy-real": Pairing(device_id="irin-test", doctor_id="buddy-real", doctor_display_name="Alex", doctor_pk=real_pk,
                                  status="paired", peer_kind="buddy", is_demo=False),
            "doc-1": Pairing(device_id="irin-test", doctor_id="doc-1", doctor_display_name="Dr. Patel", doctor_pk=doc_pk,
                             status="paired", peer_kind="doctor", is_demo=True),
        }
        s = self.eng.settings
        s.night_buddy.have_buddy = True
        s.night_buddy.hub_watchable = True
        s.emergency_script = EmergencyScript(steps=["Call him twice", "If no answer, ring his sister"])
        self.pending: list = []
        self.alerts: list[dict] = []
        self.updates: list[dict] = []
        self.rung = BuddyRung(
            settings=s, alarm_state=lambda: self.eng.state, recorder=self.rec,
            recipients=lambda d: [p for p in self.pairs.values() if p.status == "paired" and p.is_demo == d],
            post_card=self.client.post_card, post_hub=self.client.post_hub, spawn=self.pending.append,
            device_id="irin-test", is_demo=lambda: self.demo, brain_only=lambda: self.brain,
            first_name=lambda: "Chris", on_alert=self.alerts.append, on_update=self.updates.append,
            cloud_url="http://cloud.test", cloud_device_id="irin-test", cloud_device_token="dev-tok",
            relay_url="http://relay.test", source_key="k", transport=self.relay.transport())
        self.eng.on_transition(self.rung)

    def feed(self, mgdl):
        r = Reading(timestamp=clock.now(), glucose_mgdl=mgdl, trend="Flat", source="replay")
        self.eng.process_reading(r)
        self.rung.on_reading(r)

    def advance(self, minutes, present=None):
        """30 s ticks, in main.py's order: engine deadlines, the radar sample, then the rung."""
        for _ in range(int(minutes * 2)):
            clock.advance(30)
            self.eng.tick()
            self.rec.sample(present)
            self.rung.tick()

    def drain(self):
        async def run(coros):
            return await asyncio.gather(*coros)

        coros, self.pending[:] = list(self.pending), []
        return asyncio.run(run(coros)) if coros else []

    def open_card(self, env) -> dict:
        box = Box(PrivateKey(crypto.unb64(self.buddy_sk)), PublicKey(crypto.unb64(crypto.device_public_key())))
        return json.loads(box.decrypt(crypto.unb64(env["ciphertext"]), crypto.unb64(env["nonce"])))

    def events(self) -> list[str]:
        return [e["kind"] for _, e in store.select_buddy_event_rows()]


@pytest.fixture
def rig(tmp_path, monkeypatch):
    r = Rig(tmp_path, monkeypatch)
    yield r
    while r.pending:  # every spawned send is awaited (a delivery spawns its notifies)
        r.drain()
    clock.reset()


def _numbers(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _numbers(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _numbers(v)
    else:
        yield obj


def test_full_alarm_home_unacked_past_t10_fires_one_alert_sealed_to_the_buddy_without_glucose(rig):
    rig.feed(61.7)  # the actual-low crossing: a full alarm
    rig.advance(9.5, present=True)  # through the 5-minute strobe step, still under T+10
    assert rig.alerts == [] and rig.rung.alert is None
    rig.advance(1, present=True)
    assert len(rig.alerts) == 1
    rig.drain()
    [env] = rig.relay.cards  # only the demo buddy: never the doctor, never the real-world buddy
    assert env["recipient_id"] == "buddy-demo" and env["kind"] == "buddy_alert" and env["program"] == "buddy"
    assert env["is_demo"] is True and env["source"] == "irin_bedside"
    payload = rig.open_card(env)
    assert payload == rig.alerts[0] == rig.rung.alert
    alert, listing = payload["alert"], payload["listing"]
    assert alert["confidence"] == "device_confirmed" and alert["status"] == "open" and alert["audio_url"] is None
    assert alert["event_id"].startswith("ae-20200101T0300") and alert["event_id"].endswith("-actual_low")
    assert alert["urgency"] == 1
    assert payload["message"] == "Chris's low alarm has gone unanswered for 10 minutes."
    assert listing["first_name"] == "Chris" and listing["is_demo"] is True and listing["elapsed_min"] == 10
    text = json.dumps(payload).lower()
    assert "61.7" not in text and "mgdl" not in text and "glucose" not in text  # invariant 15
    assert 61.7 not in list(_numbers(payload))
    [(route, body)] = rig.relay.hub
    assert route == "listing" and body["listing_id"] == listing["listing_id"] and body["is_demo"] is True
    assert body["script"] == {"steps": ["Call him twice", "If no answer, ring his sister"]}
    assert "61.7" not in json.dumps(body) and "mgdl" not in json.dumps(body)
    rig.advance(5, present=True)
    rig.drain()
    assert len(rig.alerts) == 1 and len(rig.relay.cards) == 1  # one alert per stretch
    # additive only: the local alarm is exactly where alarm.py put it, still sounding
    assert rig.eng.state.state == "active" and rig.hal.playing == "alarm_urgent"
    assert rig.events() == ["alert"]
    assert rig.rung.state()["link"] == {"first_name": "Sam", "mode": "twin", "peer_id": "buddy-demo"}


@pytest.mark.parametrize("present", [False, None])
def test_never_fires_when_the_room_is_empty_or_the_radar_unknown(rig, present):
    rig.feed(62)
    rig.advance(25, present=present)
    rig.drain()
    assert rig.alerts == [] and rig.relay.cards == [] and rig.relay.hub == []
    assert rig.rung.emergency is None and rig.eng.state.state == "active"


def test_no_hub_listing_without_hub_watchable_and_no_alert_without_have_buddy(rig):
    rig.eng.settings.night_buddy.hub_watchable = False
    rig.eng.settings.night_buddy.have_buddy = False
    rig.feed(62)
    rig.advance(10.5, present=True)
    rig.drain()
    assert len(rig.alerts) == 1 and rig.relay.cards == [] and rig.relay.hub == []  # opt-ins gate delivery


def test_brain_only_fires_only_on_a_confirmed_level_2_low_and_labels_unconfirmed(rig):
    rig.brain = True
    rig.feed(62)  # level 1: never enough without presence
    rig.advance(12)
    assert rig.alerts == []
    rig.feed(52)  # one reading under 54 is not confirmed
    rig.advance(5)
    rig.feed(50)  # the second: confirmed, sustained from the first
    rig.advance(4.5)
    assert rig.alerts == []
    rig.advance(1)
    [payload] = rig.alerts
    assert payload["alert"]["confidence"] == "unconfirmed" and payload["listing"]["confidence"] == "unconfirmed"
    rig.drain()
    assert rig.relay.cards[0]["source"] == "irin_brain"


def test_brain_only_never_fires_after_any_acknowledgement(rig):
    rig.brain = True
    rig.feed(50)
    rig.feed(49)
    rig.eng.acknowledge("app")
    rig.advance(16)  # re-armed at +15, still low
    rig.feed(48)
    rig.advance(12)
    assert rig.eng.state.state in ("rearmed", "active") and rig.alerts == []


def test_claim_call_and_treating_leave_the_t20_clock_untouched_and_it_fires_at_t20(rig):
    rig.feed(62)
    rig.advance(10.5, present=True)
    assert rig.rung.alert is not None
    t = set_treating(rig.rung)  # the patient's one press
    handle_calls(rig.rung, [{"listing_id": "hl-x", "claim_id": "c1", "at": "t"}], rig.hal, lambda: rig.eng.state, 0.8)
    rig.drain()
    assert ("treating", {"listing_id": rig.rung.listing_id}) in rig.relay.hub
    assert t["since"] and t["expires_at"] and rig.rung.state()["treating"] == t
    rig.advance(9, present=True)  # T+19.5
    assert rig.rung.emergency is None
    rig.advance(0.5, present=True)  # T+20
    assert rig.rung.emergency is not None and rig.rung.emergency["text"] == EMERGENCY_TEXT
    assert rig.rung.state()["emergency"] == rig.rung.emergency
    assert any(u.get("event") == "emergency" for u in rig.updates)
    assert rig.events() == ["alert", "treating", "call", "emergency"]
    assert rig.eng.state.state == "active" and rig.hal.playing == "alarm_urgent"  # never quieted


def test_recovery_resolves_the_listing_and_disarms_the_clock(rig):
    rig.feed(62)
    rig.advance(10.5, present=True)
    listing_id = rig.rung.alert["listing"]["listing_id"]
    rig.feed(80)
    rig.feed(95)  # two readings back at or above the threshold: the engine closes the episode
    assert rig.eng.state.state == "idle"
    rig.advance(15, present=True)
    rig.drain()
    assert ("resolve", {"listing_id": listing_id, "outcome": "recovered"}) in rig.relay.hub
    assert rig.rung.alert is None and rig.rung.emergency is None
    assert rig.events() == ["alert", "resolved"]


def test_ack_resolves_and_the_rung_reclimbs_after_a_rearm(rig):
    rig.feed(62)
    rig.advance(10.5, present=True)
    first = rig.rung.alert["listing"]["listing_id"]
    rig.eng.acknowledge("device")
    rig.drain()
    assert ("resolve", {"listing_id": first, "outcome": "acknowledged"}) in rig.relay.hub and rig.rung.alert is None
    rig.advance(15, present=True)  # still low 15 min after the ack: re-armed
    assert rig.eng.state.state == "rearmed"
    rig.advance(9.5, present=True)
    assert len(rig.alerts) == 1
    rig.advance(1, present=True)
    assert len(rig.alerts) == 2 and rig.alerts[1]["alert"]["urgency"] == 2


def test_a_call_plays_the_non_alarm_chime_and_never_stops_an_alarm(rig):
    assert BUDDY_CHIME in SOUND_NAMES and BUDDY_CHIME not in ("alarm_soft", "alarm_urgent", "chirp")
    rig.client.on_calls = lambda calls: handle_calls(rig.rung, calls, rig.hal, lambda: rig.eng.state, 0.8)
    rig.feed(62)
    rig.advance(10.5, present=True)
    rig.hal.calls.clear()
    rig.relay.calls = [{"listing_id": "hl-1", "claim_id": "c1", "at": "2020-01-01T03:11:00"}]
    asyncio.run(rig.client.poll())
    # the one-channel driver would cut the alarm: the chime waits out a sounding low
    assert rig.hal.playing == "alarm_urgent" and ("stop_sound",) not in rig.hal.calls
    assert rig.updates[-1] == {"listing_id": "hl-1", "event": "call", "chimed": False}
    rig.eng.acknowledge("app")  # quiet now
    rig.relay.calls.append({"listing_id": "hl-1", "claim_id": "c1", "at": "2020-01-01T03:12:00"})
    asyncio.run(rig.client.poll())  # the first call is not replayed; the new one chimes
    assert rig.hal.playing == BUDDY_CHIME and rig.eng.state.state == "acknowledged"
    assert [c for c in rig.hal.calls if c[0] == "play_sound"][-1] == ("play_sound", BUDDY_CHIME, 0.8)
    assert rig.updates[-1] == {"listing_id": "hl-1", "event": "call", "chimed": True}
    assert rig.events().count("call") == 2


def test_demo_alerts_never_reach_a_non_demo_pairing(rig):
    del rig.pairs["buddy-demo"]  # only the real-world buddy is paired
    rig.feed(62)
    rig.advance(10.5, present=True)
    rig.drain()
    assert len(rig.alerts) == 1 and rig.relay.cards == []
    assert all(body["is_demo"] is True for _, body in rig.relay.hub)


def test_a_live_alert_goes_only_to_the_live_buddy(tmp_path, monkeypatch):
    r = Rig(tmp_path, monkeypatch, demo=False)
    try:
        r.feed(62)
        r.advance(10.5, present=True)
        r.drain()
        assert [c["recipient_id"] for c in r.relay.cards] == ["buddy-real"] and r.relay.cards[0]["is_demo"] is False
        r.drain()  # the notify the delivery spawned
        assert [b["peer_id"] for b, _ in r.relay.notifies] == ["buddy-real"]
    finally:
        clock.reset()


AUDIO = "https://cloud.example.test/v1/audio/" + "a" * 64 + ".mp3"


def test_b4_audio_url_from_the_cloud_rides_the_sealed_alert_and_notify_is_posted(rig):
    rig.relay.audio_url = AUDIO
    rig.feed(61.7)
    rig.advance(10.5, present=True)
    assert rig.alerts[0]["alert"]["audio_url"] is None  # the local broadcast never waits on the cloud
    rig.drain()  # the delivery: render, seal, post
    [(render, rh)] = rig.relay.renders
    assert render == {"kind": "buddy_alert",
                      "text": "Your buddy Chris is in trouble. The alarm has been unacknowledged for ten minutes."}
    assert rh["x-device-id"] == "irin-test" and rh["x-device-token"] == "dev-tok"
    [env] = rig.relay.cards
    assert rig.open_card(env)["alert"]["audio_url"] == AUDIO
    assert rig.rung.alert["alert"]["audio_url"] == AUDIO
    rig.drain()  # the notify the delivery spawned
    [(body, nh)] = rig.relay.notifies
    assert body == {"peer_id": "buddy-demo", "first_name": "Chris", "minutes": 10, "audio_url": AUDIO, "is_demo": True}
    assert nh["x-source-key"] == "k"
    text = json.dumps([render, body]).lower()
    assert "61.7" not in text and "mg" not in text and "glucose" not in text  # invariant 15


@pytest.mark.parametrize("failure", ["down", "hangs", "500"])
def test_b4_cloud_down_or_slow_still_sends_the_alert_with_audio_url_null(rig, monkeypatch, failure):
    monkeypatch.setattr(rung_mod, "AUDIO_TIMEOUT_S", 0.05)
    base = rig.relay.transport()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/audio/render":
            if failure == "down":
                raise httpx.ConnectError("refused", request=request)
            if failure == "hangs":
                await asyncio.Event().wait()  # never answers; the rung's timeout cancels it
            return httpx.Response(500)
        return await base.handle_async_request(request)

    rig.rung.transport = httpx.MockTransport(handler)
    rig.feed(61.7)
    rig.advance(10.5, present=True)
    t0 = time.monotonic()
    rig.drain()
    assert time.monotonic() - t0 < 1.0  # bounded by the timeout, never by the cloud
    [env] = rig.relay.cards
    assert rig.open_card(env)["alert"]["audio_url"] is None
    rig.drain()
    assert [b["audio_url"] for b, _ in rig.relay.notifies] == [None]


def test_b4_no_cloud_url_skips_the_clip_and_the_alert_still_goes(rig):
    rig.rung.cloud_url = ""
    rig.feed(62)
    rig.advance(10.5, present=True)
    rig.drain()
    assert rig.relay.renders == [] and len(rig.relay.cards) == 1


def test_b4_voice_text_spells_minutes_and_carries_no_digits():
    assert rung_mod.number_words(12) == "twelve" and rung_mod.number_words(21) == "twenty-one"
    assert rung_mod.number_words(120) == "one hundred and twenty"
    t = rung_mod.alert_voice_text("Chris", 11)
    assert t == "Your buddy Chris is in trouble. The alarm has been unacknowledged for eleven minutes."
    assert not any(ch.isdigit() for ch in t)


def test_treating_without_an_alert_is_refused(rig):
    with pytest.raises(TreatingError) as e:
        set_treating(rig.rung)
    assert e.value.status == 409


def test_the_rung_only_observes_alarm_py():
    src = inspect.getsource(rung_mod) + inspect.getsource(treating_mod)
    assert "AlarmEngine" not in src and ".acknowledge(" not in src and "process_" not in src
    assert "stop_sound" not in src and "alarm_urgent" not in src and "alarm_soft" not in src
    assert "time.time" not in src and "sleep(" not in src
    from app import alarm

    alarm_src = inspect.getsource(alarm)
    assert not any("buddy" in line for line in alarm_src.splitlines() if line.startswith(("from ", "import ")))
    assert "get_presence" not in alarm_src


def test_treating_endpoint_pin_gated_409_then_broadcasts_treating_set(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from app import auth, main

    monkeypatch.setattr(auth.config, "PIN", "1234")
    sent = []

    async def capture(msg):
        sent.append(msg)

    with TestClient(main.app) as c:
        monkeypatch.setattr(main.hub, "broadcast", capture)
        assert c.post("/api/buddy/treating").status_code == 401
        assert c.post("/api/buddy/treating", headers={"X-PIN": "1234"}).status_code == 409
        b = main.runtime.buddy
        b.alert = {"alert": {"alert_id": "ba-x", "confidence": "device_confirmed"}, "listing": {"listing_id": "hl-x"},
                   "message": "x"}
        try:
            r = c.post("/api/buddy/treating", headers={"X-PIN": "1234"})
            assert r.status_code == 200 and set(r.json()) == {"since", "expires_at"}
            assert [m.type for m in sent] == ["treating_set"] and sent[0].payload == r.json()
            with c.websocket_connect("/ws") as ws:
                snap = json.loads(ws.receive_text())
            assert snap["type"] == "state_snapshot"
            bs = snap["payload"]["buddy_state"]
            assert set(bs) == {"link", "open_alert", "treating", "morning_line", "emergency", "matches", "my_buddy"}
            assert bs["treating"] == r.json() and bs["open_alert"] == b.alert
        finally:
            b.alert = b.treating = None


def test_demo_buddy_rung_stages_presence_and_a_held_low(monkeypatch):
    from fastapi.testclient import TestClient

    from app import auth, main

    monkeypatch.setattr(auth.config, "PIN", "1234")
    with TestClient(main.app) as c:
        try:
            assert c.post("/api/demo/buddy_rung").status_code == 401
            r = c.post("/api/demo/buddy_rung", headers={"X-PIN": "1234"})
            assert r.status_code == 200 and r.json()["status"] == "staged" and r.json()["presence_driven"] is True
            assert r.json()["injected"]["glucose_mgdl"] == 55 and r.json()["feed_paused"] is True
            assert main.runtime.outputs.hal.get_presence() is True
        finally:
            main.runtime.outputs.hal.set_presence_for_test(None)
            main.runtime.datasource = main.make_datasource(main.runtime.mode)  # no overlay or pause leaks


# --- B5: the buddy lines ---

from datetime import date, timedelta  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from app.rounds import narrative  # noqa: E402

NIGHT = date(2019, 12, 31)  # the night T0 (03:00) belongs to, keyed by its evening like the ledger


def _record(coverage=95.0, demo=True, alarm_event_ids=(), minutes_below_70=0):
    return SimpleNamespace(night_date=NIGHT, window_start=datetime(2019, 12, 31, 22, 0),
                           window_end=datetime(2020, 1, 1, 7, 0), coverage_pct=coverage, is_demo=demo,
                           alarm_event_ids=list(alarm_event_ids), minutes_below_70=minutes_below_70)


def _no_model(monkeypatch):
    """NARRATIVE_BACKEND=template (conftest): no link may be called."""
    for name in ("_call_backboard", "_call_meta", "_call_anthropic"):
        monkeypatch.setattr(narrative, name, lambda *a, **kw: pytest.fail("a model link was called"))


def _lines(rig):
    return [(env, rig.open_card(env)) for env in rig.relay.cards if env["kind"] == "buddy_line"]


def test_b5_morning_line_after_a_quiet_night_is_the_template_sealed_to_the_buddy(rig, monkeypatch):
    _no_model(monkeypatch)
    rig.rung.on_night(_record())
    rig.drain()
    quiet = narrative._buddy_line_template({"kind": "all_quiet"}, {})
    assert rig.rung.state()["morning_line"] == quiet
    [(env, payload)] = _lines(rig)  # the demo buddy only: never the doctor, never the real-world buddy
    assert env["recipient_id"] == "buddy-demo" and env["program"] == "buddy" and env["is_demo"] is True
    assert env["card_id"] == "bl-2019-12-31"
    assert payload == {"line": quiet, "kind": "buddy_line", "night_date": "2019-12-31", "line_kind": "morning", "listing_id": None}
    assert {"event": "morning", "line": quiet, "night_date": "2019-12-31"} in rig.updates  # live to an open app


def test_b5_close_out_after_a_resolved_episode_broadcast_sealed_and_kept_for_the_morning(rig, monkeypatch):
    _no_model(monkeypatch)
    rig.feed(61.7)
    rig.advance(10.5, present=True)
    set_treating(rig.rung)
    handle_calls(rig.rung, [{"listing_id": "hl-x", "claim_id": "c1", "at": "t"}], rig.hal, lambda: rig.eng.state, 0.8)
    call_at = [e for _, e in store.select_buddy_event_rows() if e["kind"] == "call"][0]["at"]
    rig.feed(80)
    rig.feed(95)  # recovery closes the episode
    while rig.pending:
        rig.drain()
    h = datetime.fromisoformat(call_at)
    line = f"Chris's okay. Your call at {h.hour % 12 or 12}:{h.minute:02d} got through. Chris treated and recovered."
    assert rig.rung.state()["morning_line"] == line
    listing_id = rig.alerts[0]["listing"]["listing_id"]
    assert {"listing_id": listing_id, "event": "resolved", "line": line} in rig.updates
    [(env, payload)] = _lines(rig)
    assert env["recipient_id"] == "buddy-demo" and payload["line"] == line and payload["night_date"] == "2019-12-31"
    assert payload["line_kind"] == "close_out" and payload["listing_id"]  # the watcher closes exactly this alert
    ids = ("listing_id", "event_id")  # ids carry timestamp digits; the check is for glucose values in the words
    text = json.dumps({k: v for k, v in payload.items() if k not in ids}) + json.dumps(
        [{k: v for k, v in u.items() if k not in ids} for u in rig.updates if "line" in u])
    assert "61.7" not in text and "80" not in text and "95" not in text and "mg" not in text.lower()
    # the next ledger row keeps the close-out as that night's line, re-sent under the same card_id
    rig.rung.on_night(_record())
    rig.drain()
    assert rig.rung.state()["morning_line"] == line
    assert {env["card_id"] for env, _ in _lines(rig)} == {"bl-2019-12-31"}


def test_b5_a_night_with_an_alert_but_no_close_out_never_says_okay(rig, monkeypatch):
    _no_model(monkeypatch)
    rig.feed(62)
    rig.advance(10.5, present=True)
    rig.rung.reset()  # a mode switch is not a resolution: no close-out
    rig.drain()
    assert not any("line" in u for u in rig.updates) and rig.rung.morning is None
    rig.rung.on_night(_record())
    rig.drain()
    assert "okay" not in rig.rung.state()["morning_line"] and "quiet" not in rig.rung.state()["morning_line"].lower()


@pytest.mark.parametrize("coverage,expected", [(0.0, rung_mod.NO_DATA_LINE), (60.0, rung_mod.PARTIAL_LINE)])
def test_b5_a_night_irin_could_not_see_is_never_told_as_fine(rig, monkeypatch, coverage, expected):
    _no_model(monkeypatch)
    rig.rung.on_night(_record(coverage))
    rig.drain()
    assert rig.rung.state()["morning_line"] == expected
    assert "fine" not in expected and "all quiet" not in expected.lower()


def test_b5_demo_lines_never_reach_a_real_pairing_and_live_lines_only_the_live_buddy(rig, monkeypatch):
    _no_model(monkeypatch)
    rig.rung.on_night(_record(demo=True))
    rig.drain()
    rig.demo = False
    rig.rung.on_night(_record(demo=False))
    rig.drain()
    assert [(env["recipient_id"], env["is_demo"]) for env in rig.relay.cards if env["kind"] == "buddy_line"] == [
        ("buddy-demo", True), ("buddy-real", False)]


def test_b5_no_line_is_sent_without_have_buddy(rig, monkeypatch):
    _no_model(monkeypatch)
    rig.eng.settings.night_buddy.have_buddy = False
    rig.rung.on_night(_record())
    rig.drain()
    assert _lines(rig) == [] and rig.rung.state()["morning_line"] is not None


@pytest.mark.parametrize("ids,below", [(["ae-20191231T230000000000-actual_low"], 0),
                                       (["ae-20191231T230000000000-predicted_low"], 0), ([], 12)])
def test_b5_a_night_with_a_low_that_never_reached_the_rung_is_never_all_quiet(rig, monkeypatch, ids, below):
    """Rehearsal: a night whose low was answered (or loaded by a seek) read "all quiet with 0 alerts"."""
    _no_model(monkeypatch)
    rig.rung.on_night(_record(alarm_event_ids=ids, minutes_below_70=below))
    rig.drain()
    line = rig.rung.state()["morning_line"]
    assert line == rung_mod.LOW_LINE and "quiet" not in line.lower()


def test_b5_a_stale_or_high_alarm_alone_still_reads_quiet(rig, monkeypatch):
    _no_model(monkeypatch)
    rig.rung.on_night(_record(alarm_event_ids=["ae-20191231T230000000000-stale", "ae-20191231T231000000000-high"]))
    rig.drain()
    assert rig.rung.state()["morning_line"] == narrative._buddy_line_template({"kind": "all_quiet"}, {})
