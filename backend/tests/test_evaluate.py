"""R8 engine check through the app: seeded nights make Basal Check fire, the
card is sealed to the paired demo doctor and reaches the fake relay; a
second evaluation inside 14 days is suppressed by the budget; the red rule
re-runs when an alarm episode closes; the 07:05 job is registered."""

import json
from datetime import date, datetime, timedelta

import httpx
from fastapi.testclient import TestClient

from app import auth, main, store
from app.contracts import NightRecord, Pairing
from app.rounds import crypto
from tests.test_cards import FakeRelay

H = {"X-PIN": "1234"}


def seed_nights(today: date, rises):
    for i, r in enumerate(rises):
        d = today - timedelta(days=len(rises) - i)
        store.upsert_night_record(NightRecord(
            night_date=d, window_start=datetime.combine(d, datetime.min.time()).replace(hour=22),
            window_end=datetime.combine(d + timedelta(days=1), datetime.min.time()).replace(hour=7),
            coverage_pct=96.0, reason_codes=["clean"] if r is not None else ["late_meal"], code_source="logged",
            rise_mgdl=r if r is not None else 55.0, low_point_mgdl=100.0, tbr_pct=0.0, is_demo=True))


def test_basal_check_fires_once_and_reaches_the_paired_doctor(monkeypatch):
    monkeypatch.setattr(auth.config, "PIN", "1234")
    relay = FakeRelay()
    with TestClient(main.app) as c:
        main.runtime.relay_client.transport = relay.transport()
        main.runtime.relay_client.relay_url, main.runtime.relay_client.source_key = "http://relay.test", "k"
        _, doc_pk = crypto.generate_keypair()
        main.runtime.pairing.pairings["doc-8"] = Pairing(device_id="irin-dev", doctor_id="doc-8", doctor_display_name="Dr",
                                                          doctor_pk=doc_pk, status="paired", is_demo=True)
        today = main.runtime.ledger.night_ended_on(main.clock.now().date())
        seed_nights(today, [42, 45, 50, 40, 42, 44, -5, -10, None, None, None, None, None, None])
        assert "evaluate" in [j["name"] for j in c.get("/api/scheduler").json()["jobs"]]
        r = c.post("/api/rounds/evaluate", json={"today": today.isoformat()}, headers=H)
        assert r.status_code == 200
        by_kind = {e["kind"]: e for e in r.json()}
        assert by_kind["basal_check"]["status"] == "amber" and by_kind["basal_check"]["budget"] == "sent"
        assert by_kind["basal_check"]["sent"]["recipients"] == ["doc-8"]
        assert by_kind["hypo_response"]["status"] == "green" and by_kind["hypo_response"]["budget"] == "digest"
        assert [e["kind"] for e in relay.cards] == ["basal_check"] and relay.cards[0]["is_demo"] is True
        stored = c.get("/api/rounds/cards").json()
        assert stored[0]["card"]["kind"] == "basal_check" and stored[0]["card"]["confidence"]["clean_nights"] == "measured"
        assert c.get("/api/rounds/evaluations").json()["basal_check"]["budget"] == "sent"
        # the same morning again (a re-evaluation, a seek): the budget holds it
        r = c.post("/api/rounds/evaluate", json={"today": today.isoformat()}, headers=H)
        assert {e["kind"]: e["budget"] for e in r.json()}["basal_check"] == "interval" and len(relay.cards) == 1
        assert c.post("/api/rounds/evaluate", json={}).status_code == 401
        main.runtime.pairing.pairings.pop("doc-8")
        main.runtime.standing.last.clear()


def test_red_rule_reruns_when_an_alarm_episode_closes(monkeypatch):
    monkeypatch.setattr(auth.config, "PIN", "1234")
    relay = FakeRelay()
    with TestClient(main.app) as c:
        main.runtime.relay_client.transport = relay.transport()
        main.runtime.relay_client.relay_url, main.runtime.relay_client.source_key = "http://relay.test", "k"
        _, doc_pk = crypto.generate_keypair()
        main.runtime.pairing.pairings["doc-8"] = Pairing(device_id="irin-dev", doctor_id="doc-8", doctor_display_name="Dr",
                                                          doctor_pk=doc_pk, status="paired", is_demo=True)
        today = main.runtime.ledger.night_ended_on(main.clock.now().date())
        seed_nights(today, [10] * 6)
        from app.contracts import Reading
        from app.clock import clock

        def reading(m):
            return Reading(timestamp=clock.now(), glucose_mgdl=m, trend="Flat", source="replay")

        eng, rec = main.runtime.alarm, main.runtime.alarm_events
        for _ in range(2):  # two unacknowledged, escalated actual lows -> two re-arms? no: two escalated episodes
            eng.process_reading(reading(58))
            clock.advance(6 * 60)
            eng.tick()  # the strobe: escalated
            eng.acknowledge("app")
            clock.advance(16 * 60)
            eng.tick()
            eng.process_reading(reading(85))
            eng.process_reading(reading(119))  # the episode closes -> on_event -> the red rule
        import time as _t

        for _ in range(50):  # the scheduled coroutine runs on the app loop
            if relay.cards:
                break
            c.get("/api/health")
            _t.sleep(0.02)
        kinds = [e["kind"] for e in relay.cards]
        assert kinds == ["hypo_response"], kinds  # once: the 12 h cap holds the second episode
        assert relay.cards[0]["kind"] == "hypo_response"
        main.runtime.pairing.pairings.pop("doc-8")
        main.runtime.standing.last.clear()
