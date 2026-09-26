"""Step 12 check: switch live -> demo -> live in tests and assert alarm reset,
badge state (mode in the snapshot and mode_change), and that inject-low
(and every other demo control) 404s in live mode; pause makes the feed
stale honestly; injections are an overlay and the CSV replays clean.
Plus the settings endpoint the app's form saves through."""

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import auth, main
from app.clock import clock
from app.contracts import Reading, Settings

H = {"X-PIN": "1234"}


@pytest.fixture
def client(monkeypatch, tmp_path):
    from app import store

    saved = main.runtime.settings.model_dump()  # the one shared Settings object: restore it after
    monkeypatch.setattr(auth.config, "PIN", "1234")
    monkeypatch.setattr(store.config, "IRIN_DB", str(tmp_path / "t.db"))
    monkeypatch.setattr(main.config, "NIGHTSCOUT_URL", "https://ns.invalid")
    with TestClient(main.app) as c:
        yield c
    restored = Settings.model_validate(saved)
    for name in Settings.model_fields:
        setattr(main.runtime.settings, name, getattr(restored, name))
    main.runtime.presence.set_override("auto")
    main.runtime.datasource = main.make_datasource(main.runtime.mode)  # no overlay, pause, or speed leaks
    from app import demo

    demo._live_basal_time = None


def snapshot(c) -> dict:
    with c.websocket_connect("/ws") as ws:
        return json.loads(ws.receive_text())["payload"]


def test_live_demo_live_switch_resets_alarm_and_badges(client):
    c = client
    assert snapshot(c)["mode"] == "replay"  # DEMO badge on
    # a low while in demo: the alarm is active
    r = c.post("/api/demo/inject_low", json={"glucose_mgdl": 55}, headers=H)
    assert r.status_code == 200
    main.runtime.alarm.process_reading(Reading.model_validate(r.json()["injected"]))
    assert c.get("/api/alarm").json()["state"] == "active"

    # -> live: alarm reset to idle, badge off, demo controls gone
    r = c.post("/api/mode", json={"mode": "nightscout"}, headers=H)
    assert r.status_code == 200 and snapshot(c)["mode"] == "nightscout"
    assert c.get("/api/alarm").json()["state"] == "idle"
    for path, body in [("/api/demo/inject_low", {"glucose_mgdl": 55}), ("/api/demo/speed", {"speed": 60}),
                       ("/api/demo/pause", {"paused": True}), ("/api/demo/scenario", {"name": "the_save"}),
                       ("/api/demo/basal_time", {})]:
        assert c.post(path, json=body, headers=H).status_code == 404, path

    # -> demo again: badge back, alarm still idle, scenario from the top
    r = c.post("/api/mode", json={"mode": "replay"}, headers=H)
    assert r.status_code == 200 and snapshot(c)["mode"] == "replay"
    assert c.get("/api/alarm").json()["state"] == "idle"
    assert c.get("/api/latest").json()["source"] == "replay"


def test_demo_controls_are_pin_gated_even_in_demo(client):
    assert client.post("/api/demo/inject_low", json={"glucose_mgdl": 55}).status_code == 401


def test_scenario_list_and_select(client):
    r = client.get("/api/demo/scenarios")
    assert r.status_code == 200 and "the_save" in r.json()["scenarios"] and r.json()["current"] == "the_save"
    assert client.post("/api/demo/scenario", json={"name": "nope"}, headers=H).status_code == 404
    assert client.post("/api/demo/scenario", json={"name": "../etc"}, headers=H).status_code == 422
    r = client.post("/api/demo/scenario", json={"name": "the_save"}, headers=H)
    assert r.status_code == 200 and abs((clock.now() - main.runtime.datasource.rows[0][0]).total_seconds()) < 5  # the clock restarted at the first row


def test_speed_changes_multiplier_without_moving_the_clock(client):
    before = clock.now()
    r = client.post("/api/demo/speed", json={"speed": 120}, headers=H)
    assert r.status_code == 200 and clock.speed == 120
    assert abs((clock.now() - before).total_seconds()) < 5
    assert client.post("/api/demo/speed", json={"speed": 0}, headers=H).status_code == 422


def test_pause_makes_the_feed_stale_honestly(client):
    assert client.post("/api/demo/pause", json={"paused": True}, headers=H).status_code == 200
    first = client.get("/api/latest").json()
    clock.advance(15 * 60)
    later = client.get("/api/latest").json()
    assert later["timestamp"] == first["timestamp"] and later["is_stale"] is True
    client.post("/api/demo/pause", json={"paused": False}, headers=H)
    resumed = client.get("/api/latest").json()
    assert resumed["timestamp"] > first["timestamp"] and resumed["is_stale"] is False


def test_inject_is_an_overlay_and_the_csv_stays_clean(client):
    ds = main.runtime.datasource
    n_rows = len(ds.rows)
    r = client.post("/api/demo/inject_low", json={"glucose_mgdl": 52}, headers=H)
    assert r.status_code == 200
    latest = client.get("/api/latest").json()
    assert latest["glucose_mgdl"] == 52 and latest["source"] == "replay"
    assert len(ds.rows) == n_rows  # the CSV rows are untouched
    hist = client.get("/api/history?minutes=60").json()
    assert hist[-1]["glucose_mgdl"] == 52
    clock.advance(5 * 60)  # the next CSV reading is newer than the overlay
    assert client.get("/api/latest").json()["glucose_mgdl"] != 52


def test_basal_time_button_arms_the_nudge_and_never_reaches_live(client):
    c = client
    assert c.post("/api/settings", json={"basal_time": "21:30"}, headers=H).status_code == 200  # the real one
    r = c.post("/api/demo/basal_time", headers=H)
    assert r.status_code == 200
    expect = (clock.now() - timedelta(minutes=61)).strftime("%H:%M")
    assert r.json()["basal_time"] in (expect, (clock.now() - timedelta(minutes=62)).strftime("%H:%M"))
    assert snapshot(c)["settings"]["basal_time"] == r.json()["basal_time"]
    c.post("/api/demo/basal_time", headers=H)  # pressed twice: the stash still holds the real value
    assert c.post("/api/mode", json={"mode": "nightscout"}, headers=H).status_code == 200
    assert c.get("/api/settings").json()["basal_time"] == "21:30"  # live nudge ladder runs on the real time
    c.post("/api/mode", json={"mode": "replay"}, headers=H)


def test_inject_while_paused_shows_at_once(client):
    c = client
    assert c.post("/api/demo/pause", json={"paused": True}, headers=H).status_code == 200
    r = c.post("/api/demo/inject_low", json={"glucose_mgdl": 45}, headers=H)
    assert r.status_code == 200 and c.get("/api/latest").json()["glucose_mgdl"] == 45
    assert c.post("/api/demo/inject_low", json={"glucose_mgdl": 45, "trend": "<script>"}, headers=H).status_code == 422


def test_demo_treatments_stay_local(client):
    """Treatments logged in demo mode are stored locally and never posted to
    Nightscout: there is no Nightscout write path anywhere in the backend."""
    import inspect

    from app import main as m, store, voice
    from app.datasource import nightscout

    src = inspect.getsource(m) + inspect.getsource(store) + inspect.getsource(voice) + inspect.getsource(nightscout)
    assert "treatments.json" not in src and "api/v1/treatments" not in src


# --- settings ---


def test_settings_partial_update_reaches_every_engine_and_the_snapshot(client):
    c = client
    assert c.get("/api/settings").json()["night_window_end"] == "07:00"
    assert c.post("/api/settings", json={"night_window_end": "08:00"}).status_code == 401
    r = c.post("/api/settings", json={"night_window_end": "08:00", "basal_time": "21:30"}, headers=H)
    assert r.status_code == 200 and r.json()["night_window_end"] == "08:00" and r.json()["low_threshold"] == 70
    assert snapshot(c)["settings"]["night_window_end"] == "08:00"
    assert main.runtime.alarm.settings is main.runtime.settings  # the same object everywhere
    jobs = {j["name"]: j["at"] for j in c.get("/api/scheduler").json()["jobs"]}
    assert jobs["morning_report"] == "08:00"  # the report job moved with the window
    r = c.post("/api/settings", json={"presence_override": "away"}, headers=H)
    assert r.status_code == 200 and c.get("/api/presence").json()["mode"] == "away"
    # nested objects merge field by field: one opt-in never resets the other three
    c.post("/api/settings", json={"night_buddy": {"have_buddy": True}}, headers=H)
    r = c.post("/api/settings", json={"night_buddy": {"be_watcher": True}}, headers=H)
    assert r.json()["night_buddy"]["have_buddy"] is True and r.json()["night_buddy"]["be_watcher"] is True
    r = c.post("/api/settings", json={"led_colors": {"ambient": "#000000"}}, headers=H)
    assert r.json()["led_colors"]["ambient"] == "#000000" and len(r.json()["led_colors"]) > 1


def test_settings_rejects_bad_values(client):
    c = client
    # invariant 3: a NaN or absurd threshold would make the actual-low alarm never fire
    for body in ('{"low_threshold": NaN}', '{"low_threshold": -Infinity}', '{"high_threshold": Infinity}'):
        r = c.post("/api/settings", content=body, headers={**H, "Content-Type": "application/json"})
        assert r.status_code == 422, body
    for body in ({"low_threshold": 20}, {"low_threshold": 150}, {"high_threshold": 5000}, {"volume": 99},
                 {"consecutive_predictions_n": 0}, {"iob_duration_hours": -1}, {"basal_units": 0}):
        assert c.post("/api/settings", json=body, headers=H).status_code == 422, body
    assert c.get("/api/settings").json()["low_threshold"] == 70
    assert c.post("/api/settings", json={"night_window_end": "25:00"}, headers=H).status_code == 422
    assert c.post("/api/settings", json={"basal_time": "9pm"}, headers=H).status_code == 422
    assert c.post("/api/settings", json={"low_threshold": 300}, headers=H).status_code == 422
    assert c.post("/api/settings", json={"volume": "loud"}, headers=H).status_code == 422
    assert c.post("/api/settings", json={"nope": 1}, headers=H).status_code == 400
    assert c.post("/api/settings", json=[1, 2], headers=H).status_code in (400, 422)
    assert c.get("/api/settings").json()["night_window_end"] == "07:00"  # nothing applied


def test_demo_basal_time_never_reaches_the_saved_live_settings(client):
    """Press the demo basal button, change an unrelated setting (which saves the
    whole Settings), switch to live, restart: the live basal_time comes back."""
    c = client
    assert c.post("/api/settings", json={"basal_time": "21:30"}, headers=H).status_code == 200
    assert c.post("/api/demo/basal_time", headers=H).status_code == 200
    assert c.post("/api/settings", json={"volume": 0.5}, headers=H).status_code == 200  # persists while the demo value is live
    assert c.post("/api/mode", json={"mode": "nightscout"}, headers=H).status_code == 200
    assert c.get("/api/settings").json()["basal_time"] == "21:30"
    for name in Settings.model_fields:  # a restart: memory forgets, the store remembers
        setattr(main.runtime.settings, name, getattr(Settings(), name))
    main._load_settings()
    assert main.runtime.settings.basal_time == "21:30" and main.runtime.settings.volume == 0.5
    c.post("/api/mode", json={"mode": "replay"}, headers=H)


def test_presence_override_is_not_persisted_so_a_reboot_starts_in_auto(client):
    c = client
    assert c.post("/api/presence", json={"override": "away"}, headers=H).status_code == 200
    setattr(main.runtime.settings, "presence_override", "auto")
    main._load_settings()
    assert main.runtime.settings.presence_override == "auto"
