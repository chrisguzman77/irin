"""Audit fixes: the tunnel gate on every /api read and on /ws, and the PIN throttle."""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

TOKEN = "t" * 43
TUNNEL = {"Cf-Connecting-Ip": "203.0.113.9"}
MODE = {"mode": "replay"}


@pytest.fixture
def client(monkeypatch):
    from app import auth, main

    monkeypatch.setattr(auth.config, "PIN", "1234")
    with TestClient(main.app) as c:
        monkeypatch.setattr(main, "token_matches", lambda t: t == TOKEN)
        monkeypatch.setattr(auth, "token_matches", lambda t: t == TOKEN)
        yield c


def test_tunnel_read_needs_the_owner_bearer(client):
    r = client.get("/api/latest", headers=TUNNEL)
    assert r.status_code == 401 and r.json() == {"detail": "pair this phone with your Irin"}
    assert client.get("/api/latest", headers={**TUNNEL, "Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/api/latest", headers={**TUNNEL, "Authorization": f"Bearer {TOKEN}"}).status_code == 200


def test_health_and_owner_stay_open_through_the_tunnel(client):
    assert client.get("/api/health", headers=TUNNEL).status_code == 200
    assert client.get("/api/owner", headers=TUNNEL).status_code == 200


def test_no_tunnel_header_is_unchanged_and_options_passes(client):
    assert client.get("/api/latest").status_code == 200
    r = client.options("/api/latest", headers={**TUNNEL, "Origin": "http://localhost:5173",
                                              "Access-Control-Request-Method": "GET"})
    assert r.status_code == 200


def test_ws_through_the_tunnel_needs_the_subprotocol_token(client):
    with client.websocket_connect("/ws") as ws:  # no tunnel: open
        assert ws.receive_json()
    for protos in (None, ["irin.owner", "irin.token.wrong"]):
        with pytest.raises(WebSocketDisconnect) as e:
            with client.websocket_connect("/ws", headers=dict(TUNNEL), subprotocols=protos):
                pass
        assert e.value.code == 4401
    with client.websocket_connect("/ws", headers=dict(TUNNEL), subprotocols=["irin.owner", f"irin.token.{TOKEN}"]) as ws:
        assert ws.accepted_subprotocol == "irin.owner"
        assert ws.receive_json()


def _bad(c, **h):
    return c.post("/api/mode", json=MODE, headers={"X-PIN": "0000", **h})


def _good(c, **h):
    return c.post("/api/mode", json=MODE, headers={"X-PIN": "1234", **h})


def test_pin_throttle_locks_after_five_and_unlocks_after_the_window(client, monkeypatch):
    from app import auth

    now = [1000.0]
    monkeypatch.setattr(auth.time, "monotonic", lambda: now[0])
    for _ in range(5):
        assert _bad(client).status_code == 401
    r = _good(client)
    assert r.status_code == 429 and r.json()["detail"] == "too many PIN attempts; wait 10 minutes"
    now[0] += 599
    assert _bad(client).status_code == 429
    now[0] += 2
    assert _good(client).status_code == 200


def test_correct_pin_resets_the_count_and_clients_are_separate(client):
    for _ in range(4):
        assert _bad(client).status_code == 401
    assert _good(client).status_code == 200
    for _ in range(4):
        assert _bad(client).status_code == 401  # count restarted
    other = {"Cf-Connecting-Ip": "198.51.100.1", "Authorization": f"Bearer {TOKEN}"}
    for _ in range(5):
        _bad(client, **other)
    assert _bad(client, **other).status_code == 429
    assert _bad(client).status_code == 401  # the first client is not locked


def test_localhost_never_locks(client):
    local = TestClient(client.app, client=("127.0.0.1", 5000))
    for _ in range(8):
        assert _bad(local).status_code == 401
    assert _good(local).status_code == 200


def test_review_fixes_head_health_open_docs_gated_empty_pin_not_a_guess():
    from fastapi.testclient import TestClient
    from app import auth, main
    with TestClient(main.app) as c:
        tunnel = {"Cf-Connecting-Ip": "203.0.113.9"}
        assert c.head("/api/health", headers=tunnel).status_code != 401
        assert c.get("/openapi.json", headers=tunnel).status_code == 401
        assert c.get("/openapi.json").status_code == 200  # the LAN and the kiosk keep the schema
        auth._fails.clear(); auth._locked_until.clear()
        req = type("R", (), {"headers": {"Cf-Connecting-Ip": "198.51.100.7"}, "client": None})()
        for _ in range(8):
            try:
                auth._check("", req)
            except Exception:
                pass
        assert "198.51.100.7" not in auth._locked_until  # empty PINs never lock the owner out
