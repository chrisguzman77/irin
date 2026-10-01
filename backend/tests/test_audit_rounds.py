"""Audit fixes: a red Hypo Response during a Step Watch is absorbed into the watch's red
safety card (invariant 10), never dropped; amber stays suppressed; the SYNTHETIC flag
reaches /api/health, the WS snapshot payload and the sealed card payload."""

import asyncio
import json
from datetime import date, datetime
from pathlib import Path

from app import main, store
from app.clock import clock
from app.contracts import Settings
from app.rounds import cards as cards_mod
from app.rounds.evaluate import StandingEngine
from app.rounds.standing import Evaluation
from tests.test_step_watch import FakeSender, Sources, db, plan, watch  # noqa: F401

SCENARIOS = Path(__file__).resolve().parents[2] / "demo" / "scenarios"


def hypo(status, flags=("level2",)):
    return Evaluation(kind="hypo_response", status=status, flags=list(flags),
                      metrics={"lows": 2, "lows_unfelt": 1}, confidence={"lows": "measured", "lows_unfelt": "reported"},
                      headline="Two lows in the window.", period_start=date(2020, 1, 1), period_end=date(2020, 1, 14))


def rig():
    sender = FakeSender()
    sw = watch(Sources(), sender=sender)
    engine = StandingEngine(settings=Settings(), sender=FakeSender(), device_id="irin-test", is_demo=lambda: True)
    engine.active_watch = lambda: sw.active_plan() is not None
    engine.absorb = sw.absorb_hypo
    return sw, engine, sender


def test_a_red_hypo_response_during_a_watch_is_merged_into_the_red_safety_card(db):
    clock.set(speed=60.0, start=datetime(2020, 1, 15, 7, 5))
    store.upsert_plan(plan())
    sw, engine, sender = rig()
    engine.evaluations = lambda today: [hypo("red")]
    out = asyncio.run(engine.run(today=date(2020, 1, 15), event_key="ae-9"))
    assert out[0]["budget"] == "watch" and out[0]["sent"] is None  # no Standing Card
    [(card, key)] = sender.sent
    assert key == "ae-9" and card.program == "step_watch" and card.kind == "safety" and card.status == "red"
    assert "level2" in card.flags
    assert card.metrics["hypo_response_lows"] == 2 and card.confidence["hypo_response_lows_unfelt"] == "reported"
    assert out[0]["absorbed"]["sent"]["status"] == "sent"
    asyncio.run(engine.run(today=date(2020, 1, 15), event_key="ae-9"))  # the same episode is told once
    assert len(sender.sent) == 1


def test_amber_and_green_hypo_response_during_a_watch_stay_suppressed(db):
    clock.set(speed=60.0, start=datetime(2020, 1, 15, 7, 5))
    store.upsert_plan(plan())
    sw, engine, sender = rig()
    for status in ("amber", "green"):
        engine.evaluations = lambda today, s=status: [hypo(s)]
        out = asyncio.run(engine.run(today=date(2020, 1, 15)))
        assert "absorbed" not in out[0]
    assert sender.sent == []


def test_synthetic_follows_the_active_scenario(monkeypatch):
    monkeypatch.setattr(main.runtime, "mode", "replay")
    monkeypatch.setattr(main.runtime.datasource, "path", SCENARIOS / "titration_synthetic.csv", raising=False)
    assert main.runtime.is_synthetic() is True
    monkeypatch.setattr(main.runtime.datasource, "path", SCENARIOS / "failure.csv", raising=False)  # no companion
    assert main.runtime.is_synthetic() is False
    monkeypatch.setattr(main.runtime.datasource, "path", SCENARIOS / "titration_synthetic.csv", raising=False)
    monkeypatch.setattr(main.runtime, "mode", "nightscout")
    assert main.runtime.is_synthetic() is False


def test_health_and_ws_snapshot_carry_synthetic(monkeypatch):
    from fastapi.testclient import TestClient

    with TestClient(main.app) as c:
        monkeypatch.setattr(main.runtime, "mode", "replay")
        monkeypatch.setattr(main.runtime.datasource, "path", SCENARIOS / "titration_synthetic.csv", raising=False)
        assert c.get("/api/health").json()["synthetic"] is True
        with c.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
        assert msg["type"] == "state_snapshot" and msg["payload"]["synthetic"] is True
        monkeypatch.setattr(main.runtime.datasource, "path", SCENARIOS / "failure.csv", raising=False)
        assert c.get("/api/health").json()["synthetic"] is False


def test_a_card_built_while_synthetic_carries_the_flag_in_its_sealed_payload(db, monkeypatch):
    sealed = []
    monkeypatch.setattr(cards_mod.crypto, "seal", lambda body, pk: sealed.append(json.loads(body)) or {"nonce": "n", "ciphertext": "c"})

    class P:
        doctor_id, doctor_pk = "doc-1", "pk"

    async def post(env):
        return True

    for flag in (True, False):
        sender = cards_mod.CardSender(recipients=lambda demo: [P()], post=post, device_id="irin-test", synthetic=lambda f=flag: f)
        card = cards_mod.assemble(program="standing", kind="basal_check", status="amber", flags=[], metrics={"nights": 14},
                                  confidence={"nights": "measured"}, period_start=date(2020, 1, 1), period_end=date(2020, 1, 14),
                                  headline="h", device_id="irin-test", is_demo=True)
        asyncio.run(sender.send(card))
    assert sealed[0]["synthetic"] is True and "synthetic" not in sealed[1]
