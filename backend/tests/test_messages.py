"""R9 check: a doctor message never applies without confirm; decline and
expiry apply nothing; a message from a non-paired key is rejected; silence
changes nothing; a confirmed insulin_change writes exactly one
therapy_change Treatment, updates basal_units, and marks the change date
Follow-up compares around; the resolution word reaches the relay; the
confirm route is a FRESH-PIN route; is_demo must match the pairing."""

import asyncio
import json
from datetime import date, datetime, timedelta

import pytest
from nacl.public import Box, PrivateKey, PublicKey
from nacl.utils import random as nacl_random

from app import store
from app.clock import clock
from app.contracts import DoctorMessage, Pairing, Settings
from app.rounds import crypto
from app.rounds.messages import DoctorMessages, MessageError, therapy_change_key

THERAPY_CHANGE_KEY = therapy_change_key(True)

T0 = datetime(2020, 1, 15, 8, 0)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setattr(store.config, "IRIN_DB", str(tmp_path / "t.db"))
    store.init_db()
    monkeypatch.setattr(crypto, "KEYS_DIR", tmp_path / "keys")
    clock.set(speed=60.0, start=T0)
    doc_sk_b64, doc_pk = crypto.generate_keypair()
    doc_sk = PrivateKey(crypto.unb64(doc_sk_b64))
    pairings = {"doc-1": Pairing(device_id="irin-test", doctor_id="doc-1", doctor_display_name="Dr. Patel",
                                 doctor_pk=doc_pk, status="paired", is_demo=True)}
    settings = Settings(basal_units=22.0)
    resolutions, received, resolved = [], [], []

    async def post_resolution(mid, status):
        resolutions.append((mid, status))
        return True

    svc = DoctorMessages(settings=settings, pairings=lambda: pairings, post_resolution=post_resolution,
                         on_received=received.append, on_resolved=resolved.append, is_demo=lambda: True)

    def seal(msg: DoctorMessage, sk=doc_sk, is_demo=True, sender="doc-1"):
        nonce = nacl_random(Box.NONCE_SIZE)
        ct = Box(sk, PublicKey(crypto.unb64(crypto.device_public_key()))).encrypt(msg.model_dump_json().encode(), nonce).ciphertext
        return {"message_id": msg.message_id, "sender_id": sender, "nonce": crypto.b64(nonce), "ciphertext": crypto.b64(ct),
                "kind": msg.kind, "is_demo": is_demo, "device_id": "irin-test"}

    yield svc, seal, settings, pairings, resolutions, received, resolved
    clock.reset()


def basal_change(mid="m1"):
    return DoctorMessage(message_id=mid, kind="insulin_change", insulin="basal", new_units=24.0,
                         start_date=date(2020, 1, 16), created_at=T0)


def therapy_changes():
    return [t for t in store.select_treatments(T0 - timedelta(days=1)) if t.kind == "therapy_change"]


def test_received_message_is_pending_and_applies_nothing(rig):
    svc, seal, settings, *_ , received, _ = rig
    [msg] = svc.receive([seal(basal_change())])
    assert msg.message_id == "m1" and svc.pending_messages()[0].status == "pending"
    assert settings.basal_units == 22.0 and therapy_changes() == [] and store.get_kv(THERAPY_CHANGE_KEY) is None
    assert received[0]["status"] == "pending" and received[0]["doctor_display_name"] == "Dr. Patel"
    assert svc.receive([seal(basal_change())]) == []  # the same id again: skipped


def test_confirm_applies_once_and_posts_the_resolution(rig):
    svc, seal, settings, pairings, resolutions, received, resolved = rig
    svc.receive([seal(basal_change())])
    doc = asyncio.run(svc.confirm("m1"))
    assert doc["message"]["status"] == "confirmed" and settings.basal_units == 24.0
    [t] = therapy_changes()
    assert t.insulin_units == 24.0 and t.confirmed and "basal 24 u from 2020-01-16" in t.dose_label
    assert store.get_kv(THERAPY_CHANGE_KEY) == "2020-01-16" and store.get_kv(therapy_change_key(False)) is None
    assert resolutions == [("m1", "confirmed")] and resolved[0]["status"] == "confirmed"
    with pytest.raises(MessageError) as e:
        asyncio.run(svc.confirm("m1"))
    assert e.value.status == 409 and len(therapy_changes()) == 1  # exactly one


def test_decline_and_expiry_apply_nothing(rig):
    svc, seal, settings, pairings, resolutions, *_ = rig
    svc.receive([seal(basal_change("m1")), seal(basal_change("m2"))])
    assert asyncio.run(svc.decline("m1"))["message"]["status"] == "declined"
    clock.advance(24 * 3600 + 60)
    assert asyncio.run(svc.expire()) == 1
    with pytest.raises(MessageError) as e:
        asyncio.run(svc.confirm("m2"))
    assert e.value.status in (409, 410)
    assert settings.basal_units == 22.0 and therapy_changes() == [] and store.get_kv(THERAPY_CHANGE_KEY) is None
    assert sorted(resolutions) == [("m1", "declined"), ("m2", "expired")]
    assert svc.pending() == []


def test_silence_changes_nothing(rig):
    svc, seal, settings, *_ = rig
    svc.receive([seal(basal_change())])
    clock.advance(6 * 3600)
    assert asyncio.run(svc.expire()) == 0 and settings.basal_units == 22.0 and therapy_changes() == []


def test_unpaired_or_tampered_or_mismatched_messages_are_rejected(rig):
    svc, seal, settings, pairings, resolutions, received, _ = rig
    other_sk = PrivateKey.generate()
    assert svc.receive([seal(basal_change("x1"), sk=other_sk)]) == []  # sealed by an unpaired key
    assert svc.receive([seal(basal_change("x2"), sender="doc-9")]) == []  # unknown sender
    env = seal(basal_change("x3"))
    raw = bytearray(crypto.unb64(env["ciphertext"]))
    raw[-1] ^= 1
    env["ciphertext"] = crypto.b64(bytes(raw))
    assert svc.receive([env]) == []  # tampered
    assert svc.receive([seal(basal_change("x4"), is_demo=False)]) == []  # a real message to a demo pairing
    env = seal(basal_change("x5"))
    env["kind"] = "note"
    assert svc.receive([env]) == []  # envelope metadata lies about the kind
    pairings["doc-1"] = pairings["doc-1"].model_copy(update={"status": "revoked", "doctor_pk": ""})
    assert svc.receive([seal(basal_change("x6"))]) == []  # revoked
    assert received == [] and resolutions == [] and store.select_doctor_messages() == []


def test_plan_messages_go_to_the_step_watch_hook_and_notes_apply_nothing(rig):
    svc, seal, settings, *_ = rig
    handed = []
    svc.on_plan_message = handed.append
    hold = DoctorMessage(message_id="h1", kind="hold_step", hold_weeks=2, plan_id="p1", created_at=T0)
    note = DoctorMessage(message_id="n1", kind="note", text="see you Tuesday", created_at=T0)
    svc.receive([seal(hold), seal(note)])
    asyncio.run(svc.confirm("h1"))
    asyncio.run(svc.confirm("n1"))
    assert [m.message_id for m in handed] == ["h1"] and therapy_changes() == [] and settings.basal_units == 22.0


def test_endpoints_are_fresh_pin_and_the_snapshot_lists_pending(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from app import auth, main
    from app.contracts import FRESH_PIN_ENDPOINTS

    monkeypatch.setattr(auth.config, "PIN", "1234")
    assert "/api/rounds/messages/{message_id}/confirm" in FRESH_PIN_ENDPOINTS
    assert "/api/rounds/messages/{message_id}/decline" in FRESH_PIN_ENDPOINTS
    with TestClient(main.app) as c:
        doc_sk_b64, doc_pk = crypto.generate_keypair()
        main.runtime.pairing.pairings["doc-7"] = Pairing(device_id="irin-dev", doctor_id="doc-7", doctor_display_name="Dr",
                                                          doctor_pk=doc_pk, status="paired", is_demo=True)
        msg = basal_change("e1")
        nonce = nacl_random(Box.NONCE_SIZE)
        ct = Box(PrivateKey(crypto.unb64(doc_sk_b64)), PublicKey(crypto.unb64(crypto.device_public_key()))).encrypt(
            msg.model_dump_json().encode(), nonce).ciphertext
        main.runtime.messages.receive([{"message_id": "e1", "sender_id": "doc-7", "nonce": crypto.b64(nonce),
                                        "ciphertext": crypto.b64(ct), "kind": "insulin_change", "is_demo": True}])
        assert c.get("/api/rounds/messages").status_code == 401  # plaintext doctor content: PIN
        assert [m["message"]["message_id"] for m in c.get("/api/rounds/messages", headers={"X-PIN": "1234"}).json()] == ["e1"]
        with c.websocket_connect("/ws") as ws:
            snap = json.loads(ws.receive_text())["payload"]
            assert snap["pending_doctor_messages"][0]["message_id"] == "e1"
        assert c.post("/api/rounds/messages/e1/confirm").status_code == 401
        r = c.post("/api/rounds/messages/e1/confirm", headers={"X-PIN": "1234"})
        assert r.status_code == 200 and r.json()["message"]["status"] == "confirmed"
        assert c.get("/api/settings").json()["basal_units"] == 24.0
        assert c.get("/api/rounds/messages", headers={"X-PIN": "1234"}).json() == []
        assert c.post("/api/rounds/messages/e1/decline", headers={"X-PIN": "1234"}).status_code == 409
        main.runtime.pairing.pairings.pop("doc-7")
        main.runtime.settings.basal_units = None


def test_malformed_or_unbounded_messages_are_refused_at_receive(rig):
    svc, seal, settings, *_ = rig
    bad = [DoctorMessage(message_id="b1", kind="insulin_change", insulin="basal", new_units=-5.0, created_at=T0),
           DoctorMessage(message_id="b2", kind="insulin_change", insulin="basal", new_units=500.0, created_at=T0),
           DoctorMessage(message_id="b3", kind="insulin_change", insulin="basal", new_units=float("nan"), created_at=T0),
           DoctorMessage(message_id="b4", kind="insulin_change", created_at=T0),
           DoctorMessage(message_id="b5", kind="hold_step", hold_weeks=3, plan_id="p1", created_at=T0)]
    assert svc.receive([seal(m) for m in bad]) == [] and svc.pending() == []


def test_the_other_worlds_message_waits_and_cannot_be_answered_here(rig):
    svc, seal, settings, pairings, *_ = rig
    pairings["doc-2"] = Pairing(device_id="irin-test", doctor_id="doc-2", doctor_display_name="Dr. Real",
                                doctor_pk=pairings["doc-1"].doctor_pk, status="paired", is_demo=False)
    assert svc.receive([seal(basal_change("r1"), is_demo=False, sender="doc-2")]) == []  # device is in demo: left on the relay
    svc.receive([seal(basal_change("d1"))])
    svc.is_demo = lambda: False  # the mode switch
    with pytest.raises(MessageError) as e:
        asyncio.run(svc.confirm("d1"))
    assert e.value.status == 409 and settings.basal_units == 22.0
    clock.advance(6 * 3600)
    assert asyncio.run(svc.expire()) == 0  # the demo message does not expire under the live clock either
    svc.is_demo = lambda: True
    assert asyncio.run(svc.confirm("d1"))["message"]["status"] == "confirmed"


def test_a_revoked_doctors_pending_message_never_applies(rig):
    svc, seal, settings, pairings, resolutions, *_ = rig
    svc.receive([seal(basal_change())])
    pairings["doc-1"] = pairings["doc-1"].model_copy(update={"status": "revoked", "doctor_pk": ""})
    with pytest.raises(MessageError) as e:
        asyncio.run(svc.confirm("m1"))
    assert e.value.status == 410 and settings.basal_units == 22.0 and therapy_changes() == []
    assert svc.pending() == []


def test_a_lost_receipt_is_retried_on_the_next_tick(rig):
    svc, seal, settings, pairings, resolutions, *_ = rig
    calls = {"n": 0}

    async def flaky(mid, status):
        calls["n"] += 1
        return calls["n"] > 1  # the relay is down the first time

    svc.post_resolution = flaky
    svc.receive([seal(basal_change())])
    doc = asyncio.run(svc.confirm("m1"))
    assert doc["resolution_posted"] is False and settings.basal_units == 24.0
    asyncio.run(svc.expire())
    assert store.select_doctor_message("m1")["resolution_posted"] is True and calls["n"] == 2


def test_a_past_start_date_is_clamped_to_the_confirm_day(rig):
    svc, seal, settings, *_ = rig
    old = DoctorMessage(message_id="o1", kind="insulin_change", insulin="basal", new_units=20.0,
                        start_date=date(2019, 12, 1), created_at=T0)
    svc.receive([seal(old)])
    asyncio.run(svc.confirm("o1"))
    assert store.get_kv(THERAPY_CHANGE_KEY) == T0.date().isoformat()
