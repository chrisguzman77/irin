"""B4+ check: the buddy clip renders once per text (the second call makes no
ElevenLabs call), VOICE_BACKEND=none returns null, a glucose-looking text is
422, the device token is required, and the clip is served by its hash. A fake
ElevenLabs transport: no network, no database."""

import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import audio  # noqa: E402
import main  # noqa: E402
from main import app  # noqa: E402

H = {"X-Device-Id": "irin-test-0001", "X-Device-Token": "tok-1234"}
TEXT = "Your buddy Chris is in trouble. The alarm has been unacknowledged for twelve minutes."


@pytest.fixture
def fake(monkeypatch, tmp_path):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, content=b"ID3fake-mp3", headers={"content-type": "audio/mpeg"})

    monkeypatch.setattr(main, "DEVICE_ID", H["X-Device-Id"])
    monkeypatch.setattr(main, "DEVICE_TOKEN", H["X-Device-Token"])
    monkeypatch.setattr(audio, "VOICE_BACKEND", "elevenlabs")
    monkeypatch.setattr(audio, "ELEVENLABS_API_KEY", "el-test")
    monkeypatch.setattr(audio, "ELEVENLABS_VOICE_ALERT", "voice-alert")
    monkeypatch.setattr(audio, "DOMAIN", "example.test")
    monkeypatch.setattr(audio, "CACHE_DIR", tmp_path / "audio_cache")
    monkeypatch.setattr(audio, "TRANSPORT", httpx.MockTransport(handler))
    return calls


def test_render_caches_and_the_second_call_makes_no_api_call(fake):
    with TestClient(app) as c:
        r1 = c.post("/v1/audio/render", json={"kind": "buddy_alert", "text": TEXT}, headers=H)
        assert r1.status_code == 200, r1.text
        url = r1.json()["audio_url"]
        assert url.startswith("https://cloud.example.test/v1/audio/") and url.endswith(".mp3")
        assert len(fake) == 1
        req = fake[0]
        assert req.url.path == "/v1/text-to-speech/voice-alert" and req.headers["xi-api-key"] == "el-test"
        r2 = c.post("/v1/audio/render", json={"kind": "buddy_alert", "text": TEXT}, headers=H)
        assert r2.json()["audio_url"] == url and len(fake) == 1  # a cache hit costs no API call
        clip = c.get("/v1/audio/" + url.rsplit("/", 1)[1])  # public, no credential
        assert clip.status_code == 200 and clip.content == b"ID3fake-mp3"
        assert clip.headers["content-type"] == "audio/mpeg"
        assert c.get("/v1/audio/" + "0" * 64 + ".mp3").status_code == 404
        assert c.get("/v1/audio/..%2Fmain.py").status_code == 404


def test_none_backend_returns_null_and_calls_nothing(fake, monkeypatch):
    monkeypatch.setattr(audio, "VOICE_BACKEND", "none")
    with TestClient(app) as c:
        r = c.post("/v1/audio/render", json={"kind": "buddy_alert", "text": TEXT}, headers=H)
        assert r.status_code == 200 and r.json() == {"audio_url": None} and fake == []


@pytest.mark.parametrize("text", ["Chris is at 54 mg/dL.", "Chris is at 54mgdl", "Chris's glucose is low",
                                  "Low: 60 MG", "reading 70 dL"])
def test_glucose_text_is_422(fake, text):
    with TestClient(app) as c:
        r = c.post("/v1/audio/render", json={"kind": "buddy_alert", "text": text}, headers=H)
        assert r.status_code == 422 and fake == []


def test_auth_required_and_kind_and_fields_pinned(fake):
    with TestClient(app) as c:
        body = {"kind": "buddy_alert", "text": TEXT}
        assert c.post("/v1/audio/render", json=body).status_code == 401
        assert c.post("/v1/audio/render", json=body, headers={**H, "X-Device-Token": "nope"}).status_code == 401
        assert c.post("/v1/audio/render", json={"kind": "other", "text": TEXT}, headers=H).status_code == 422
        assert c.post("/v1/audio/render", json={**body, "voice": "x"}, headers=H).status_code == 422
        assert fake == []


def test_elevenlabs_failure_is_502_and_caches_nothing(fake, monkeypatch):
    monkeypatch.setattr(audio, "TRANSPORT", httpx.MockTransport(lambda r: httpx.Response(500)))
    with TestClient(app) as c:
        r = c.post("/v1/audio/render", json={"kind": "buddy_alert", "text": TEXT}, headers=H)
        assert r.status_code == 502
        assert not list(audio.CACHE_DIR.glob("*.mp3")) if audio.CACHE_DIR.exists() else True
