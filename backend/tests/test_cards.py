"""R7 check: a fixture card round-trips seal -> relay -> open with the
doctor's key; card_id derives from program, kind, period, plan and step so a
re-evaluation never duplicates; every metric row is labeled and none blank;
a demo card only reaches demo pairings; a card the relay could not take is
kept and retried; the demo panel's send_card lists in the inbox shell."""

import asyncio
import json
from datetime import date, datetime
from pathlib import Path

import httpx
import pytest
from nacl.public import Box, PrivateKey, PublicKey

from app import store
from app.clock import clock
from app.contracts import Pairing, SignalCard
from app.rounds import crypto
from app.rounds.cards import CardSender, assemble, card_id, envelope, pseudonym
from app.rounds.relay_client import RelayClient

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class FakeRelay:
    def __init__(self, fail=False):
        self.cards = []
        self.fail = fail

    def transport(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if self.fail:
                raise httpx.ConnectError("down")
            if request.url.path == "/v0/cards":
                self.cards.append(json.loads(request.content))
                return httpx.Response(200, json={"stored": True})
            if request.url.path.endswith("/messages"):
                return httpx.Response(200, json={"messages": [], "pairings": []})
            return httpx.Response(404)

        return httpx.MockTransport(handler)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setattr(store.config, "IRIN_DB", str(tmp_path / "t.db"))
    store.init_db()
    monkeypatch.setattr(crypto, "KEYS_DIR", tmp_path / "keys")
    clock.set(speed=60.0, start=datetime(2020, 1, 15, 7, 5))
    relay = FakeRelay()
    client = RelayClient(relay_url="http://relay.test", source_key="k", device_id="irin-test", transport=relay.transport())
    doc_sk, doc_pk = crypto.generate_keypair()
    pairs = {"doc-1": Pairing(device_id="irin-test", doctor_id="doc-1", doctor_display_name="Dr. Patel", doctor_pk=doc_pk,
                              status="paired", peer_kind="doctor", is_demo=True)}
    sent = []
    sender = CardSender(recipients=lambda demo: [p for p in pairs.values() if p.status == "paired" and p.is_demo == demo],
                        post=client.post_card, device_id="irin-test", on_sent=sent.append)
    yield sender, relay, client, pairs, doc_sk, sent
    clock.reset()


def fixture_card() -> SignalCard:
    return SignalCard.model_validate(json.loads((FIXTURES / "signal_card_standing.json").read_text()))


def test_card_id_derives_from_program_kind_period_plan_step():
    a = card_id("standing", "basal_check", date(2020, 1, 1), date(2020, 1, 14))
    assert a == "standing:basal_check:2020-01-01:2020-01-14" == card_id("standing", "basal_check", date(2020, 1, 1), date(2020, 1, 14))
    b = card_id("step_watch", "step_check", date(2020, 1, 31), date(2020, 2, 4), plan_id="p1", step_index=1)
    assert b == "step_watch:step_check:p1:1:2020-01-31:2020-02-04" and b != card_id("step_watch", "step_check", date(2020, 1, 31), date(2020, 2, 4), "p1", 2)


def test_assemble_labels_every_row_and_drops_blank_ones():
    card = assemble(program="standing", kind="basal_check", status="amber", flags=["rise_high"],
                    metrics={"nights": 14, "clean_nights": 8, "rise_median_clean": 42.0, "same_direction_share": 0.75,
                             "median_ack_min": None, "excluded_nights": [{"night_date": "2020-01-03", "reasons": ["late_meal"]}]},
                    confidence={"clean_nights": "inferred", "rise_median_clean": "inferred", "same_direction_share": "bogus"},
                    period_start=date(2020, 1, 1), period_end=date(2020, 1, 14), headline="8 clean nights of 14.",
                    device_id="irin-test", is_demo=True, nights=[{"night_date": "2020-01-01", "rise_mgdl": None, "reason_codes": ["clean"]}])
    assert "median_ack_min" not in card.metrics  # never a blank row
    assert card.confidence == {"clean_nights": "inferred", "rise_median_clean": "inferred", "same_direction_share": "inferred"}
    assert card.nights == [{"night_date": "2020-01-01", "reason_codes": ["clean"]}]
    assert card.allowed_actions == ["adjust_basal", "schedule_visit", "ask_patient", "dismiss"]
    assert card.narrative.startswith("8 clean nights of 14.") and "(inferred)" in card.narrative
    assert card.patient_pseudonym == pseudonym("irin-test") and card.patient_pseudonym.startswith("Patient ")
    assert card.is_demo and card.card_id == "standing:basal_check:2020-01-01:2020-01-14"
    assert not any(w in card.narrative.lower() for w in ("units", "increase", "decrease"))


async def _test_fixture_card_round_trips_seal_relay_open(rig):
    sender, relay, client, pairs, doc_sk, sent = rig
    card = fixture_card()
    result = await sender.send(card)
    assert result["status"] == "sent" and result["recipients"] == ["doc-1"] and sent == [result]
    [env] = relay.cards
    assert env["recipient_id"] == "doc-1" and env["is_demo"] is True and env["card_id"] == card.card_id
    assert env["kind"] == "basal_check" and env["program"] == "standing" and "headline" not in json.dumps(env)
    opened = Box(PrivateKey(crypto.unb64(doc_sk)), PublicKey(crypto.unb64(crypto.device_public_key()))).decrypt(
        crypto.unb64(env["ciphertext"]), crypto.unb64(env["nonce"]))
    assert SignalCard.model_validate_json(opened) == card
    assert store.select_cards()[0]["status"] == "sent" and store.select_cards()[0]["card"]["card_id"] == card.card_id


async def _test_demo_card_only_to_demo_pairings_and_no_recipient_is_honest(rig):
    sender, relay, client, pairs, doc_sk, sent = rig
    real = fixture_card().model_copy(update={"is_demo": False})
    result = await sender.send(real)
    assert result["status"] == "no_recipient" and relay.cards == [] and sent == []
    pairs["doc-1"] = pairs["doc-1"].model_copy(update={"status": "revoked", "doctor_pk": ""})
    result = await sender.send(fixture_card())
    assert result["status"] == "no_recipient" and relay.cards == []


async def _test_undelivered_card_is_kept_and_retried(rig):
    sender, relay, client, pairs, doc_sk, sent = rig
    relay.fail = True
    result = await sender.send(fixture_card())
    assert result["status"] == "unsent" and sender.pending and client.failures == 1 and sent == []
    assert store.select_cards()[0]["status"] == "unsent"
    relay.fail = False
    assert await sender.flush() == 1 and sender.pending == {} and len(relay.cards) == 1 and sent
    assert await sender.flush() == 0


async def _test_re_evaluation_replaces_the_stored_card(rig):
    sender, relay, client, pairs, doc_sk, sent = rig
    await sender.send(fixture_card())
    await sender.send(fixture_card().model_copy(update={"status": "green"}))
    assert len(store.select_cards()) == 1 and store.select_cards()[0]["card"]["status"] == "green"
    assert len(relay.cards) == 2  # the relay upserts by card_id on its side


async def _test_relay_client_poll_hands_pairings_and_messages_to_observers():
    seen = {}

    def handler(request):
        return httpx.Response(200, json={"messages": [{"message_id": "m1"}], "pairings": [{"doctor_id": "doc-1", "status": "revoked"}]})

    client = RelayClient(relay_url="http://relay.test", source_key="k", device_id="irin-test", transport=httpx.MockTransport(handler),
                         on_messages=lambda m: seen.setdefault("m", m), on_pairings=lambda p: seen.setdefault("p", p))
    body = await client.poll()
    assert body["messages"] and seen["m"][0]["message_id"] == "m1" and seen["p"][0]["status"] == "revoked"
    down = RelayClient(relay_url="http://relay.invalid", source_key="k", device_id="irin-test")
    assert await down.poll() is None and down.failures == 1
    assert RelayClient(relay_url="", source_key="", device_id="").enabled is False


def test_demo_panel_send_card_reaches_a_paired_demo_doctor(monkeypatch):
    from fastapi.testclient import TestClient

    from app import auth, main

    monkeypatch.setattr(auth.config, "PIN", "1234")
    relay = FakeRelay()
    with TestClient(main.app) as c:
        main.runtime.relay_client.transport = relay.transport()
        main.runtime.relay_client.relay_url, main.runtime.relay_client.source_key = "http://relay.test", "k"
        _, doc_pk = crypto.generate_keypair()
        main.runtime.pairing.pairings["doc-9"] = Pairing(device_id="irin-dev", doctor_id="doc-9", doctor_display_name="Dr",
                                                          doctor_pk=doc_pk, status="paired", is_demo=True)
        r = c.post("/api/demo/send_card", json={"fixture": "signal_card_step"}, headers={"X-PIN": "1234"})
        assert r.status_code == 200 and r.json()["status"] == "sent" and r.json()["recipients"] == ["doc-9"]
        assert relay.cards[0]["program"] == "step_watch" and relay.cards[0]["is_demo"] is True
        assert c.get("/api/rounds/cards").json()[0]["card"]["kind"] == "step_check"
        assert c.get("/api/relay").json()["pending_cards"] == []
        main.runtime.pairing.pairings.pop("doc-9")


# pytest-asyncio is not a dependency: the async checks run under asyncio.run


def test_fixture_card_round_trips_seal_relay_open(rig):
    asyncio.run(_test_fixture_card_round_trips_seal_relay_open(rig))


def test_demo_card_only_to_demo_pairings_and_no_recipient_is_honest(rig):
    asyncio.run(_test_demo_card_only_to_demo_pairings_and_no_recipient_is_honest(rig))


def test_undelivered_card_is_kept_and_retried(rig):
    asyncio.run(_test_undelivered_card_is_kept_and_retried(rig))


def test_re_evaluation_replaces_the_stored_card(rig):
    asyncio.run(_test_re_evaluation_replaces_the_stored_card(rig))


def test_relay_client_poll_hands_pairings_and_messages_to_observers():
    asyncio.run(_test_relay_client_poll_hands_pairings_and_messages_to_observers())


def test_unsent_cards_survive_a_restart(rig):
    sender, relay, client, pairs, doc_sk, sent = rig
    relay.fail = True
    asyncio.run(sender.send(fixture_card()))
    again = CardSender(recipients=sender.recipients, post=client.post_card, device_id="irin-test")  # a new process
    assert list(again.pending) == [fixture_card().card_id]
    relay.fail = False
    assert asyncio.run(again.flush()) == 1 and relay.cards


def test_poll_cadence_is_wall_time_at_replay_speed():
    from app.rounds import relay_client as rc

    assert rc.POLL_DEMO_S * 60 == 300  # 5 wall seconds at 60x = 300 clock seconds per sleep
    import inspect

    assert "* clock.speed" in inspect.getsource(rc.RelayClient.run)


def test_pairing_state_broadcast_reaches_the_loop_from_a_worker_thread(monkeypatch):
    import json

    from fastapi.testclient import TestClient

    from app import auth, main
    from tests.test_pairing import FakeRelay
    from app.rounds.pairing import RelayPairing

    monkeypatch.setattr(auth.config, "PIN", "1234")
    relay = FakeRelay()
    with TestClient(main.app) as c:
        main.runtime.pairing.relay = RelayPairing("http://relay.test", "src-key", transport=relay.transport())
        with c.websocket_connect("/ws") as ws:
            ws.receive_text()  # the snapshot
            assert c.post("/api/pair/start", json={}, headers={"X-PIN": "1234"}).status_code == 200  # runs in a worker
            msg = json.loads(ws.receive_text())
            assert msg["type"] == "pairing_state" and msg["payload"]["status"] == "awaiting_scan"
        main.runtime.pairing.pending = None


def test_card_narrative_runs_the_chain_off_the_loop_and_falls_back_to_the_template(monkeypatch):
    """R13 wiring: with_narrative asks narrative.generate for task "card" in a worker
    thread; the template mode (every laptop and the suite) returns the template."""
    from app.rounds import narrative
    from app.rounds.cards import template_narrative, with_narrative

    card = fixture_card()
    seen = {}

    def fake(task, context, metrics):
        seen.update(task=task, on_loop=narrative._on_event_loop(), kind=context["kind"])
        return "Validated text."

    monkeypatch.setattr(narrative, "generate", fake)
    out = asyncio.run(with_narrative(card))
    assert out.narrative == "Validated text." and seen == {"task": "card", "on_loop": False, "kind": card.kind}
    monkeypatch.setattr(narrative, "generate", lambda *a: (_ for _ in ()).throw(RuntimeError("down")))
    assert asyncio.run(with_narrative(card)).narrative == card.narrative  # any failure: the stored template stands
    monkeypatch.undo()
    real = asyncio.run(with_narrative(card))  # NARRATIVE_BACKEND=template in the suite
    assert real.narrative == template_narrative(card.kind, card.headline, card.metrics, card.confidence)


def test_a_card_narrative_may_repeat_the_headline_numbers_but_not_flip_a_sign():
    """The 5 in "Rough stomach 3 of 5 days" is a code-computed count that no metric holds."""
    from app.rounds import narrative
    from app.rounds.cards import _checkable

    card = SignalCard.model_validate(json.loads((FIXTURES / "signal_card_step.json").read_text()))
    m = _checkable(card)
    assert narrative.validate("Stomach upset occurred on 3 of 5 days.", m)
    assert not narrative.validate("Stomach upset occurred on 3 of 6 days.", m)  # 6 is nowhere
    assert m.get("headline_numbers") and 22.0 not in m["headline_numbers"]  # 22 is already a metric (-22)
