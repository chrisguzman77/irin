"""Step 8 check: unauthenticated POSTs are rejected on EVERY mutating route
(walked from the app's route table, so a new endpoint cannot forget the
gate); the fresh-PIN routes carry require_fresh_pin; display.js has no code
path that sends a stored PIN to a fresh-PIN endpoint (the audit grep)."""

import re
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.auth import require_fresh_pin, require_pin
from app.contracts import FRESH_PIN_ENDPOINTS
from app.main import app

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def _deps(route: APIRoute) -> set:
    return {d.call for d in route.dependant.dependencies}


def mutating_routes() -> list[APIRoute]:
    return [r for r in app.routes if isinstance(r, APIRoute) and r.methods & MUTATING]


def test_every_mutating_route_declares_the_pin_gate():
    ungated = [r.path for r in mutating_routes() if not ({require_pin, require_fresh_pin} & _deps(r))]
    assert ungated == [], f"mutating routes without require_pin/require_fresh_pin: {ungated}"


def test_fresh_pin_routes_use_require_fresh_pin_and_nothing_else_does():
    by_path = {r.path: _deps(r) for r in mutating_routes()}
    for path in FRESH_PIN_ENDPOINTS:
        if path in by_path:  # present once its tier lands (R5 pairing, R9 messages)
            assert require_fresh_pin in by_path[path], f"{path} must use require_fresh_pin"
    for path, deps in by_path.items():
        if require_fresh_pin in deps:
            assert path in FRESH_PIN_ENDPOINTS, f"{path} uses require_fresh_pin but is not in FRESH_PIN_ENDPOINTS"


@pytest.mark.parametrize("route", mutating_routes(), ids=lambda r: f"{sorted(r.methods)[0]} {r.path}")
def test_unauthenticated_and_wrong_pin_are_rejected(route, monkeypatch):
    from app import auth

    monkeypatch.setattr(auth.config, "PIN", "1234")
    method = sorted(route.methods & MUTATING)[0]
    path = route.path.replace("{message_id}", "x").replace("{pending_id}", "x").replace("{id}", "x")
    with TestClient(app) as c:
        assert c.request(method, path, json={}).status_code == 401  # no header
        assert c.request(method, path, json={}, headers={"X-PIN": "0000"}).status_code == 401  # wrong
        assert c.request(method, path, json={}, headers={"X-PIN": "1234"}).status_code != 401  # right PIN: past the gate


def test_no_localhost_exemption(monkeypatch):
    from app import auth

    monkeypatch.setattr(auth.config, "PIN", "1234")
    with TestClient(app, base_url="http://localhost") as c:
        assert c.post("/api/acknowledge", json={"source": "app"}).status_code == 401


def test_unconfigured_pin_rejects_everything(monkeypatch):
    from app import auth

    monkeypatch.setattr(auth.config, "PIN", "")
    with TestClient(app) as c:
        assert c.post("/api/acknowledge", json={"source": "app"}, headers={"X-PIN": ""}).status_code == 503


def test_display_js_never_sends_a_stored_pin_to_a_fresh_pin_endpoint():
    """The audit grep: display.js may keep the acknowledge PIN in localStorage,
    but every fresh-PIN call must come from the keypad prompt, never storage."""
    src = Path(__file__).resolve().parents[2] / "frontend" / "display" / "display.js"
    if not src.exists():
        pytest.skip("no display.js")
    text = src.read_text()
    for path in FRESH_PIN_ENDPOINTS:
        literal = re.escape(path.split("{")[0])  # the static prefix of the route
        for m in re.finditer(literal, text):
            window = text[max(0, m.start() - 400): m.start()]
            assert "localStorage" not in window, f"display.js reaches {path} within 400 chars of a localStorage read"
