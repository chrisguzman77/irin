"""The kiosk view override and the Brain versus Bedside compare view.

Override: POST /api/display_mode (PIN) {mode: auto | detail | night} persists
in kv display_override and wins over the clock's night window; auto restores
the clock's rule; GET and /api/health carry it. Compare: GET
/api/rounds/cards/compare pairs the latest stored bedside card of each kind
with the same kind re-evaluated for the same window in brain_only, which is
never sent, sealed, or stored; its rows are never blank; brain_only is
restored afterwards, even on error."""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import auth, main, store
from app.clock import clock
from tests.test_seek import H, rig  # noqa: F401  (a fresh store, a paired demo doctor, a fake relay)

LABELS = {"measured", "reported", "inferred"}


@pytest.fixture
def kiosk(monkeypatch):
    monkeypatch.setattr(auth.config, "PIN", "1234")
    with TestClient(main.app) as c:
        clock.set(speed=1.0, start=datetime(2020, 1, 1, 14, 0))  # afternoon: the clock's rule says detail
        yield c
    main.runtime.scheduler.display_override = "auto"


def test_override_persists_and_wins_over_the_clock_and_auto_restores_it(kiosk):
    c = kiosk
    assert c.get("/api/display_mode").json() == {"mode": "detail", "override": "auto"}
    r = c.post("/api/display_mode", json={"mode": "night"}, headers=H)
    assert r.status_code == 200 and r.json() == {"mode": "night", "override": "night"}
    assert store.get_kv("display_override") == "night"
    assert main.runtime.scheduler.display_mode() == "night"  # the backlight follows this
    c.__exit__(None, None, None)  # a reboot: the lifespan reloads the override from kv
    main.runtime.scheduler.display_override = "auto"
    c.__enter__()
    assert c.get("/api/display_mode").json() == {"mode": "night", "override": "night"}
    clock.set(speed=1.0, start=datetime(2020, 1, 1, 23, 30))  # inside the night window
    assert c.post("/api/display_mode", json={"mode": "detail"}, headers=H).json() == {"mode": "detail", "override": "detail"}
    r = c.post("/api/display_mode", json={"mode": "auto"}, headers=H)
    assert r.json() == {"mode": "night", "override": "auto"}  # the clock's rule again
    clock.set(speed=1.0, start=datetime(2020, 1, 1, 14, 0))
    assert c.get("/api/display_mode").json() == {"mode": "detail", "override": "auto"}
    assert store.get_kv("display_override") == "auto"


def test_post_needs_the_pin_and_rejects_other_modes(kiosk):
    c = kiosk
    assert c.post("/api/display_mode", json={"mode": "night"}).status_code == 401
    assert c.post("/api/display_mode", json={"mode": "night"}, headers={"X-PIN": "0000"}).status_code == 401
    assert c.post("/api/display_mode", json={"mode": "morning"}, headers=H).status_code == 422
    assert c.get("/api/display_mode").json()["override"] == "auto"


def test_health_carries_display_override_beside_display_mode(kiosk):
    c = kiosk
    h = c.get("/api/health").json()
    assert h["display_mode"] == "detail" and h["display_override"] == "auto"
    c.post("/api/display_mode", json={"mode": "night"}, headers=H)
    h = c.get("/api/health").json()
    assert h["display_mode"] == "night" and h["display_override"] == "night"


# --- Brain versus Bedside compare ---


def _snapshot(c, rig):
    return len(rig.relay.cards), [d["card"]["card_id"] for d in c.get("/api/rounds/cards?limit=500").json()]


def _check_pairs(pairs: list[dict]) -> None:
    for p in pairs:
        bedside, brain = p["bedside"]["card"], p["brain"]
        assert {"card", "status", "recipients", "stored_at"} <= p["bedside"].keys()  # the row, as /api/rounds/cards
        assert p["kind"] == bedside["kind"] == brain["kind"]
        assert bedside["source"] == "irin_bedside" and brain["source"] == "irin_brain"
        assert brain["is_demo"] == bedside["is_demo"]
        assert brain["card_id"].endswith(":irin_brain") and brain["card_id"] != bedside["card_id"]
        # invariant 7: labels, never blanks
        assert brain["metrics"].keys() == bedside["metrics"].keys()
        assert brain["metrics"] == bedside["metrics"]  # every row, every value; only labels may change
        assert brain["confidence"].keys() == bedside["confidence"].keys()
        assert set(brain["confidence"].values()) <= LABELS
        assert (brain["period_start"], brain["period_end"]) == (bedside["period_start"], bedside["period_end"])


def test_compare_pairs_step_watch_cards_and_sends_and_stores_nothing(rig):
    c = rig.open()
    try:
        c.post("/api/demo/scenario", json={"name": "titration_synthetic"}, headers=H)
        assert c.post("/api/demo/seek", json={"step": 2, "day": 8}, headers=H).status_code == 200
        before = _snapshot(c, rig)
        r = c.get("/api/rounds/cards/compare")
        assert r.status_code == 200, r.text
        pairs = r.json()
        assert pairs and all(p["bedside"]["card"]["program"] == "step_watch" for p in pairs)
        kinds = [p["kind"] for p in pairs]
        assert len(kinds) == len(set(kinds)) and "step_check" in kinds
        _check_pairs(pairs)
        assert any(p["bedside"]["card"]["confidence"] != p["brain"]["confidence"] for p in pairs)
        assert _snapshot(c, rig) == before  # nothing sealed, posted, or stored
        assert main.config.IRIN_BRAIN_ONLY is False
    finally:
        c.__exit__(None, None, None)


def test_compare_pairs_standing_cards(rig):
    c = rig.open()
    try:
        c.post("/api/demo/scenario", json={"name": "basal_change_1"}, headers=H)
        assert c.post("/api/demo/seek", json={"date": "2021-01-25"}, headers=H).status_code == 200
        before = _snapshot(c, rig)
        pairs = c.get("/api/rounds/cards/compare").json()
        assert pairs and all(p["bedside"]["card"]["program"] == "standing" for p in pairs)
        _check_pairs(pairs)
        assert _snapshot(c, rig) == before
    finally:
        c.__exit__(None, None, None)


def test_brain_only_is_restored_when_the_evaluation_raises(rig, monkeypatch):
    c = rig.open()
    try:
        c.post("/api/demo/scenario", json={"name": "titration_synthetic"}, headers=H)
        assert c.post("/api/demo/seek", json={"step": 2, "day": 8}, headers=H).status_code == 200
        seen = []

        def boom(*a, **k):
            seen.append(main.config.IRIN_BRAIN_ONLY)
            raise RuntimeError("engine failed")

        with monkeypatch.context() as m:
            m.setattr(main.runtime.step_watch, "evaluate", boom)
            with pytest.raises(RuntimeError):
                c.get("/api/rounds/cards/compare")
        assert seen == [True] and main.config.IRIN_BRAIN_ONLY is False
        assert c.get("/api/rounds/cards/compare").status_code == 200
    finally:
        c.__exit__(None, None, None)
