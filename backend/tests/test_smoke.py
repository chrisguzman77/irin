"""Day-one smoke: the app imports, /api/health answers, /api/latest is a
valid Reading in replay mode, and the PIN gate sits on /api/mode."""

from fastapi.testclient import TestClient

from app.contracts import Reading
from app.main import app


def test_health_and_latest():
    with TestClient(app) as c:
        r = c.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True and body["datasource"] == "replay" and body["hw"] == "mock"

        r = c.get("/api/latest")
        assert r.status_code == 200
        reading = Reading.model_validate(r.json())
        assert reading.source == "replay"
        assert reading.is_stale is False

        r = c.get("/api/contracts/fresh_pin")
        assert r.status_code == 200 and len(r.json()["endpoints"]) == 3

        r = c.get("/openapi.json")
        assert r.status_code == 200


def test_mode_is_pin_gated():
    with TestClient(app) as c:
        r = c.post("/api/mode", json={"mode": "nightscout"})
        assert r.status_code in (401, 503)  # no PIN header; 503 when PIN is unconfigured


def test_ws_sends_state_snapshot_first_then_echoes():
    import json

    with TestClient(app) as c:
        with c.websocket_connect("/ws") as ws:
            first = json.loads(ws.receive_text())
            assert first["type"] == "state_snapshot"
            assert first["payload"]["mode"] == "replay" and first["payload"]["clock_synced"] is True
            assert first["payload"]["latest_reading"]["source"] == "replay"
            ws.send_text('{"hello": 1}')
            assert json.loads(ws.receive_text()) == {"echo": {"hello": 1}}


def test_acknowledge_is_pin_gated_and_alarm_state_is_readable():
    with TestClient(app) as c:
        assert c.post("/api/acknowledge", json={"source": "app"}).status_code in (401, 503)
        r = c.get("/api/alarm")
        assert r.status_code == 200 and r.json()["state"] == "idle"


def test_history_and_forecast_endpoints():
    with TestClient(app) as c:
        r = c.get("/api/history?minutes=180")
        assert r.status_code == 200 and isinstance(r.json(), list)
        r = c.get("/api/forecast")
        assert r.status_code == 200 and r.json()["status"] in ("ok", "suspended", "unavailable")


def test_presence_toggle_reaches_the_snapshot(monkeypatch):
    import json

    from app import auth

    monkeypatch.setattr(auth.config, "PIN", "1234")
    with TestClient(app) as c:
        r = c.post("/api/presence", json={"override": "away"}, headers={"X-PIN": "1234"})
        assert r.status_code == 200 and r.json()["mode"] == "away" and r.json()["source"] == "toggle"
        with c.websocket_connect("/ws") as ws:
            snap = json.loads(ws.receive_text())["payload"]
            assert snap["presence"]["mode"] == "away" and snap["settings"]["presence_override"] == "away"
        c.post("/api/presence", json={"override": "auto"}, headers={"X-PIN": "1234"})
        assert c.get("/api/presence").json()["mode"] == "home"
