"""B4+ check: the WhatsApp channel. A buddy bearer registers a number (a
doctor bearer is 403); notify sends the text then the audio through a fake
Graph transport, with no glucose value and the number never read back; no
number -> sent false; another device's source key 403; demo/real mismatch
409; glucose-named fields 422; revoke deletes the number. Runs against the
compose mongo in a throwaway database; never the network."""

import base64
import json
import os
import sys
import uuid
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from nacl.public import PrivateKey

os.environ.setdefault("ATLAS_URI", "mongodb://127.0.0.1:27017")
os.environ["RELAY_SOURCE_KEYS"] = "src-a,src-b"
os.environ["RELAY_DB_NAME"] = f"relay_test_{uuid.uuid4().hex[:8]}"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import notify  # noqa: E402
import store  # noqa: E402
from main import app  # noqa: E402

SRC = {"X-Source-Key": "src-a"}
PHONE = "+14045550123"
AUDIO = "https://cloud.example.test/v1/audio/" + "a" * 64 + ".mp3"


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


@pytest.fixture(scope="module", autouse=True)
def mongo():
    if store.status() != "ok":
        pytest.skip("no mongo at ATLAS_URI (start deploy/docker-compose.yml's mongo)")
    yield
    store.client().drop_database(store.DB_NAME)


@pytest.fixture
def c():
    store.client().drop_database(store.DB_NAME)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def graph(monkeypatch):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.test"}]})

    monkeypatch.setattr(notify, "WHATSAPP_TOKEN", "wa-token")
    monkeypatch.setattr(notify, "WHATSAPP_PHONE_NUMBER_ID", "1234567890")
    monkeypatch.setattr(notify, "DOMAIN", "example.test")
    monkeypatch.setattr(notify, "TRANSPORT", httpx.MockTransport(handler))
    return calls


def pair(c, is_demo=True, peer_kind="buddy"):
    token = uuid.uuid4().hex
    device, peer = PrivateKey.generate(), PrivateKey.generate()
    assert c.post("/v0/pair", json={"token": token, "device_pk": b64(bytes(device.public_key)), "is_demo": is_demo,
                                    "peer_kind": peer_kind, "device_id": "irin-test"}, headers=SRC).status_code == 200
    assert c.post(f"/v0/pair/{token}/complete", json={"doctor_pk": b64(bytes(peer.public_key)),
                                                       "doctor_display_name": "Sam"}).status_code == 200
    r = c.post(f"/v0/pair/{token}/confirm", headers=SRC)
    assert r.status_code == 200
    bearer = c.get(f"/v0/pair/{token}").json()["bearer"]
    return r.json()["doctor_id"], {"Authorization": f"Bearer {bearer}"}, token


def body(peer_id, **kw):
    return {"peer_id": peer_id, "first_name": "Chris", "minutes": 12, "audio_url": AUDIO, "is_demo": True, **kw}


def test_register_number_with_a_buddy_bearer_and_a_doctor_bearer_is_403(c):
    peer, bearer, token = pair(c)
    r = c.post("/v0/buddy/whatsapp", json={"phone": PHONE}, headers=bearer)
    assert r.status_code == 200 and r.json() == {"registered": True}
    assert store.db()["pairings"].find_one({"doctor_id": peer})["whatsapp_phone"] == PHONE
    for bad in ("4045550123", "+0404555", "+1 404 555 0123", "+1404555012345678"):
        assert c.post("/v0/buddy/whatsapp", json={"phone": bad}, headers=bearer).status_code == 422
    assert c.post("/v0/buddy/whatsapp", json={"phone": PHONE}).status_code == 401
    _, doc_bearer, _ = pair(c, peer_kind="doctor")
    assert c.post("/v0/buddy/whatsapp", json={"phone": PHONE}, headers=doc_bearer).status_code == 403
    # never read back: not by the pairing state, not by the device poll, not in the log
    seen = json.dumps(c.get(f"/v0/pair/{token}").json()) + json.dumps(c.get("/v0/device/irin-test/messages", headers=SRC).json()) \
        + json.dumps(c.get("/v0/log").json())
    assert PHONE not in seen and PHONE.lstrip("+") not in seen


def test_notify_sends_text_then_audio_without_glucose(c, graph):
    peer, bearer, _ = pair(c)
    c.post("/v0/buddy/whatsapp", json={"phone": PHONE}, headers=bearer)
    r = c.post("/v0/buddy/notify", json=body(peer), headers=SRC)
    assert r.status_code == 200 and r.json() == {"sent": True, "reason": "ok"}
    assert len(graph) == 2
    text, audio = (json.loads(g.content) for g in graph)
    for g in graph:
        assert str(g.url) == "https://graph.facebook.com/v21.0/1234567890/messages"
        assert g.headers["authorization"] == "Bearer wa-token"
    assert text["to"] == PHONE.lstrip("+") and text["type"] == "text" and text["messaging_product"] == "whatsapp"
    assert text["text"]["body"] == ("[DEMO] Irin: your buddy Chris is in trouble. The alarm has been unacknowledged "
                                    "for 12 minutes. Open https://watch.example.test")
    assert audio == {"messaging_product": "whatsapp", "to": PHONE.lstrip("+"), "type": "audio", "audio": {"link": AUDIO}}
    payload = (graph[0].content + graph[1].content).decode().lower()
    assert "mg" not in payload.replace("messaging", "") and "glucose" not in payload
    logged = json.dumps(c.get("/v0/log").json())
    assert PHONE.lstrip("+") not in logged and "your buddy" not in logged  # ids and outcomes only


def test_notify_without_audio_sends_the_text_only(c, graph):
    peer, bearer, _ = pair(c)
    c.post("/v0/buddy/whatsapp", json={"phone": PHONE}, headers=bearer)
    r = c.post("/v0/buddy/notify", json=body(peer, audio_url=None), headers=SRC)
    assert r.json() == {"sent": True, "reason": "ok"} and len(graph) == 1


def test_no_number_is_sent_false(c, graph):
    peer, _, _ = pair(c)
    r = c.post("/v0/buddy/notify", json=body(peer), headers=SRC)
    assert r.status_code == 200 and r.json() == {"sent": False, "reason": "no_number"} and graph == []


def test_unconfigured_whatsapp_is_sent_false(c, graph, monkeypatch):
    monkeypatch.setattr(notify, "WHATSAPP_TOKEN", "")
    peer, bearer, _ = pair(c)
    c.post("/v0/buddy/whatsapp", json={"phone": PHONE}, headers=bearer)
    assert c.post("/v0/buddy/notify", json=body(peer), headers=SRC).json() == {"sent": False, "reason": "not_configured"}
    assert graph == []


def test_wrong_source_key_403_mismatch_409_and_auth(c, graph):
    peer, bearer, _ = pair(c)
    c.post("/v0/buddy/whatsapp", json={"phone": PHONE}, headers=bearer)
    assert c.post("/v0/buddy/notify", json=body(peer), headers={"X-Source-Key": "src-b"}).status_code == 403
    assert c.post("/v0/buddy/notify", json=body(peer)).status_code == 401
    assert c.post("/v0/buddy/notify", json=body(peer, is_demo=False), headers=SRC).status_code == 409
    doc_peer, _, _ = pair(c, peer_kind="doctor")
    assert c.post("/v0/buddy/notify", json=body(doc_peer), headers=SRC).status_code == 409
    assert c.post("/v0/buddy/notify", json=body("buddy-nope"), headers=SRC).status_code == 404
    assert graph == []


@pytest.mark.parametrize("extra", [{"glucose": 54}, {"mgdl": 54}, {"glucose_mgdl": 54}, {"location": "x"},
                                   {"phone": PHONE}, {"note": "x"}])
def test_forbidden_or_unknown_fields_are_422(c, graph, extra):
    peer, _, _ = pair(c)
    assert c.post("/v0/buddy/notify", json={**body(peer), **extra}, headers=SRC).status_code == 422
    assert graph == []


def test_digits_in_the_name_or_a_foreign_audio_url_are_422(c, graph):
    peer, _, _ = pair(c)
    assert c.post("/v0/buddy/notify", json=body(peer, first_name="Chris 54"), headers=SRC).status_code == 422
    assert c.post("/v0/buddy/notify", json=body(peer, audio_url="https://evil.test/x.mp3"), headers=SRC).status_code == 422


def test_revoke_deletes_the_number(c, graph):
    peer, bearer, _ = pair(c)
    c.post("/v0/buddy/whatsapp", json={"phone": PHONE}, headers=bearer)
    assert c.post(f"/v0/pair/{peer}/revoke", headers=SRC).status_code == 200
    assert "whatsapp_phone" not in store.db()["pairings"].find_one({"doctor_id": peer})
