"""R12 check: seeking forward twice creates no duplicate cards; a reboot (a
fresh app over the same store) mid-window gives the same cards; the
basal-change window yields a Basal Check and, after the confirmed change, a
Follow-up; the titration scenario yields step 1 green and the step 2 amber
worked example verbatim, with its recall answer and check-ins overlaid; the
seek is demo-only and PIN-gated; the Spark offer is a pending plan message
the patient must confirm with a fresh PIN."""

import json
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from app import auth, main, store
from app.contracts import Pairing
from app.rounds import crypto
from app.rounds.catchup import Companion
from tests.test_cards import FakeRelay

H = {"X-PIN": "1234"}
WORKED = ("Step 2 (5 mg), days 3 to 7. Overnight low point down 22 mg/dL from baseline, 2 near-misses, "
          "time below range 4.0%. 1 low not remembered. Rough stomach 3 of 5 days.")


@pytest.fixture
def rig(monkeypatch, tmp_path):
    """The app over a fresh store with a paired demo doctor and a fake relay; `open()`
    starts it (again) so a reboot is a second open over the same store."""
    monkeypatch.setattr(auth.config, "PIN", "1234")
    monkeypatch.setattr(store.config, "IRIN_DB", str(tmp_path / "t.db"))
    monkeypatch.setattr(crypto, "KEYS_DIR", tmp_path / "keys")
    relay = FakeRelay()
    _, doc_pk = crypto.generate_keypair()

    class Rig:
        def open(self):
            c = TestClient(main.app)
            c.__enter__()
            main.runtime.relay_client.transport = relay.transport()
            main.runtime.relay_client.relay_url, main.runtime.relay_client.source_key = "http://relay.test", "k"
            main.runtime.pairing.pairings["doc-8"] = Pairing(device_id="irin-dev", doctor_id="doc-8", doctor_display_name="Dr",
                                                              doctor_pk=doc_pk, status="paired", is_demo=True)
            return c

    r = Rig()
    r.relay = relay
    yield r
    main.runtime.pairing.pairings.pop("doc-8", None)
    main.runtime.pairing.pairings.pop("impiricus-spark", None)
    main.runtime.standing.last.clear()
    main.config.IRIN_BRAIN_ONLY = False
    main.runtime.datasource = main.make_datasource(main.runtime.mode)


def cards(c):
    return [(d["card"]["kind"], d["card"]["period_end"], d["status"]) for d in c.get("/api/rounds/cards").json()]


def test_titration_seek_gives_the_worked_example_and_seeking_twice_sends_nothing_twice(rig):
    c = rig.open()
    try:
        assert c.post("/api/demo/scenario", json={"name": "titration_synthetic"}, headers=H).status_code == 200
        assert c.post("/api/demo/seek", json={"step": 2, "day": 8}).status_code == 401
        r = c.post("/api/demo/seek", json={"step": 2, "day": 8}, headers=H)  # the morning after step 2 day 7
        assert r.status_code == 200, r.text
        body = r.json()
        # 50 nights: the scenario's first rows (00:00-07:00 on 01-01) are the tail of the night before day one
        assert body["clock"].startswith("2020-02-19T09:00") and body["seeded"]["plan"] == 1 and body["nights_built"] == 50
        assert c.get("/api/health").json()["clock"].startswith("2020-02-19T09:0")
        # the plan is active, day 8 of step 2; nights carry the companion's inferred codes
        plan = c.get("/api/rounds/plan", headers=H).json()
        assert plan["active"] and plan["step_index"] == 1 and plan["day_in_step"] == 8 and plan["dose_label"] == "5 mg"
        nights = c.get("/api/nights?days=100").json()
        assert len(nights) == 50 and all(n["code_source"] == "inferred" and n["reason_codes"] == ["clean"]
                                         for n in nights if n["night_date"] >= "2020-01-01")  # the companion's codes overlaid
        # the cards: the step 2 check is the worked example; step 1's check was green (digest, never sent)
        sent = {k["card_id"] for k in rig.relay.cards}  # the relay sees ciphertext plus the card id and kind, never a number
        assert "step_watch:step_check:demo-tirzepatide:1:2020-02-14:2020-02-18" in sent
        assert not any(":0:2020-01-17:2020-01-21" in i for i in sent) and all(k["is_demo"] for k in rig.relay.cards)
        stored = {d["card"]["card_id"]: d for d in c.get("/api/rounds/cards").json()}
        card = stored["step_watch:step_check:demo-tirzepatide:1:2020-02-14:2020-02-18"]
        assert card["status"] == "sent" and card["card"]["headline"] == WORKED and card["card"]["is_demo"] is True
        assert card["card"]["metrics"]["unfelt_lows"] == 1 and card["card"]["metrics"]["tolerance"]["rough"] == 3
        evals = c.get("/api/rounds/evaluations").json()
        assert evals["basal_check"]["budget"] == "watch"  # the watch suspends Basal Check
        # the recall question of 02-15's low was answered by the overlay, born closed
        recalls = c.get("/api/rounds/recalls?date=2020-02-16", headers=H).json()
        assert [(i["recall"]["low_event_id"], i["recall"]["answer"]) for i in recalls] == [("low-20200216T025000", "dont_remember")]
        assert c.get("/api/rounds/checkin", headers=H).json()["recalls"] == []
        before = sorted(cards(c))
        n_relay = len(rig.relay.cards)
        # the same seek again, and a seek further on: nothing is sent twice
        assert c.post("/api/demo/seek", json={"step": 2, "day": 8}, headers=H).status_code == 409  # already there
        r = c.post("/api/demo/seek", json={"date": "2020-02-19"}, headers=H)
        assert r.status_code == 409
        assert sorted(cards(c)) == before and len(rig.relay.cards) == n_relay
        assert c.post("/api/demo/seek", json={"step": 7, "day": 1}, headers=H).status_code == 422
        assert c.post("/api/demo/seek", json={"date": "2021-01-01"}, headers=H).status_code == 422
    finally:
        c.__exit__(None, None, None)


def test_a_reboot_mid_window_gives_the_same_cards(rig):
    c = rig.open()
    try:
        c.post("/api/demo/scenario", json={"name": "titration_synthetic"}, headers=H)
        r = c.post("/api/demo/seek", json={"step": 1, "day": 10}, headers=H)  # 2020-01-24: the early check and step 1's check are past
        assert r.status_code == 200 and r.json()["nights_built"] == 24
        first = sorted(cards(c))
        n_relay = len(rig.relay.cards)
    finally:
        c.__exit__(None, None, None)
    c = rig.open()  # the reboot: a fresh engine over the same store
    try:
        c.post("/api/demo/scenario", json={"name": "titration_synthetic"}, headers=H)
        r = c.post("/api/demo/seek", json={"step": 1, "day": 10}, headers=H)
        assert r.status_code == 200 and r.json()["nights_built"] == 0 and r.json()["seeded"]["plan"] == 0
        assert sorted(cards(c)) == first and len(rig.relay.cards) == n_relay
        r = c.post("/api/demo/seek", json={"step": 2, "day": 8}, headers=H)  # and on to the worked example
        assert r.status_code == 200 and r.json()["nights_built"] == 26
        assert any(d["card"]["headline"] == WORKED for d in c.get("/api/rounds/cards").json())
    finally:
        c.__exit__(None, None, None)


def test_basal_change_seek_yields_a_basal_check_then_a_follow_up(rig):
    c = rig.open()
    try:
        c.post("/api/demo/scenario", json={"name": "basal_change_1"}, headers=H)
        r = c.post("/api/demo/seek", json={"date": "2021-02-15"}, headers=H)
        assert r.status_code == 200, r.text
        assert r.json()["seeded"]["dose_change"] == 1 and r.json()["seeded"]["alarm_events"] == 53
        kinds = {(d["card"]["kind"], d["card"]["period_end"]) for d in c.get("/api/rounds/cards").json()}
        basal = sorted(e for k, e in kinds if k == "basal_check")
        # the rise is visible before George's five Detect mornings (01-25..29) once 5 clean nights exist; one per 14 days
        assert basal and basal[0] <= "2021-01-25" and len(basal) == 2
        assert (date.fromisoformat(basal[1]) - date.fromisoformat(basal[0])).days >= 14
        follow = sorted(e for k, e in kinds if k == "follow_up")
        assert follow and follow[0] >= "2021-02-07"  # from day 7 after the confirmed change on 02-01
        # the companion's one nocturnal low was left unanswered: no answer, never fine
        recalls = c.get("/api/rounds/recalls?date=2021-02-03", headers=H).json()
        assert [i["recall"]["answer"] for i in recalls] == [None]
        assert all(d["card"]["is_demo"] for d in c.get("/api/rounds/cards").json())
    finally:
        c.__exit__(None, None, None)


def test_seek_is_demo_only_and_the_spark_offer_waits_for_a_fresh_pin(rig, monkeypatch):
    c = rig.open()
    try:
        c.post("/api/demo/scenario", json={"name": "titration_synthetic"}, headers=H)
        assert c.post("/api/demo/brain_only", json={"brain_only": True}, headers=H).json() == {"brain_only": True}
        assert c.get("/api/demo/scenarios").json()["brain_only"] is True and c.get("/api/demo/scenarios").json()["companion"] == "titration"
        c.post("/api/demo/brain_only", json={"brain_only": False}, headers=H)
        r = c.post("/api/demo/spark_offer", headers=H)
        assert r.status_code == 200 and r.json()["status"] == "pending"
        mid = r.json()["message_id"]
        pend = c.get("/api/rounds/messages?pending_only=true", headers=H).json()
        assert [p["message"]["message_id"] for p in pend] == [mid] and pend[0]["doctor_display_name"] == "Impiricus Spark (simulated)"
        assert pend[0]["message"]["kind"] == "plan_create" and pend[0]["message"]["plan"]["plan_id"] == "demo-tirzepatide"
        assert c.post("/api/demo/spark_offer", headers=H).json()["message_id"] == mid  # offered once
        assert c.get("/api/rounds/plan", headers=H).json()["active"] is False  # nothing applied until the patient confirms
        assert c.post(f"/api/rounds/messages/{mid}/confirm").status_code == 401
        r = c.post(f"/api/rounds/messages/{mid}/confirm", headers=H)  # the typed PIN (the screens re-prompt the keypad)
        assert r.status_code == 200 and r.json()["message"]["status"] == "confirmed"
        assert c.get("/api/rounds/plan", headers=H).json()["plan_id"] == "demo-tirzepatide"  # "start watch"
        assert c.post("/api/demo/buddy_rung", headers=H).status_code == 404
        # live mode: every control is gone
        c.post("/api/mode", json={"mode": "nightscout"}, headers=H)
        assert c.post("/api/demo/seek", json={"day": 2}, headers=H).status_code == 404
        assert c.post("/api/demo/spark_offer", headers=H).status_code == 404
        c.post("/api/mode", json={"mode": "replay"}, headers=H)
    finally:
        c.__exit__(None, None, None)


def test_companion_loads_every_scenario_and_missing_is_none():
    from app.demo import SCENARIOS_DIR

    t = Companion.load(SCENARIOS_DIR / "titration_synthetic.csv")
    assert t.kind == "titration" and t.synthetic and t.plan.plan_id == "demo-tirzepatide" and len(t.symptom_checks) == 35
    assert t.reason_codes[date(2020, 1, 1)] == (["clean"], "inferred") and t.recall_answers == {"low-20200216T025000": "dont_remember"}
    b = Companion.load(SCENARIOS_DIR / "basal_change_1.csv")
    assert b.kind == "basal_change" and not b.synthetic and b.dose_change["date"] == "2021-02-01" and b.plan is None
    assert Companion.load(SCENARIOS_DIR / "the_save.csv") is None
