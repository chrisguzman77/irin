"""Step 1 check: pydantic validates every model in contracts.py, the JSON
fixtures handed to Justin and George round-trip, and the invariants the
treaty encodes hold (insulin never without confirm; the literal sets)."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from app import contracts as c
from tests.examples import examples

FIXTURES = Path(__file__).resolve().parent / "fixtures"
EXAMPLES = examples()
ALL_MODELS = {name: obj for name, obj in inspect.getmembers(c, inspect.isclass)
              if issubclass(obj, BaseModel) and obj is not BaseModel and obj.__module__ == c.__name__}


def test_every_model_has_an_example():
    covered = {type(m) for m in EXAMPLES.values()}
    missing = {name for name, cls in ALL_MODELS.items() if cls not in covered and cls not in (c.AvailabilitySlot, c.CgmFeed)}
    assert not missing, f"models without an example: {sorted(missing)}"


@pytest.mark.parametrize("name", sorted(EXAMPLES))
def test_json_round_trip(name):
    model = EXAMPLES[name]
    again = type(model).model_validate_json(model.model_dump_json())
    assert again == model


@pytest.mark.parametrize("name", sorted(EXAMPLES))
def test_fixture_on_disk_validates_and_matches(name):
    path = FIXTURES / f"{name}.json"
    assert path.exists(), f"missing fixture {path.name}: run `python -m tests.fixtures.generate`"
    model = EXAMPLES[name]
    assert type(model).model_validate_json(path.read_text()) == model, f"{path.name} drifted from examples.py"


def test_insulin_requires_confirm():
    with pytest.raises(ValidationError):
        c.Treatment(timestamp=c.datetime(2020, 1, 1), kind="bolus", insulin_units=4.0, confirmed=False)
    c.Treatment(timestamp=c.datetime(2020, 1, 1), kind="bolus", insulin_units=4.0, confirmed=True)


def test_literals_reject_unknown_values():
    bad = [
        lambda: c.Reading(timestamp=c.datetime(2020, 1, 1), glucose_mgdl=100, trend="Flat", source="csv"),
        lambda: c.AlarmState(state="ringing"),
        lambda: c.LowEventRecall(low_event_id="l", asked_at=c.datetime(2020, 1, 1), answer="fine"),
        lambda: c.SignalCard.model_validate({**EXAMPLES["signal_card_standing"].model_dump(), "kind": "dose_advice"}),
        lambda: c.DoctorMessage(message_id="m", kind="apply_now", created_at=c.datetime(2020, 1, 1)),
        lambda: c.WSMessage(type="glucose"),
    ]
    for make in bad:
        with pytest.raises(ValidationError):
            make()


def test_fresh_pin_endpoints_and_ws_types():
    assert c.FRESH_PIN_ENDPOINTS == (
        "/api/pair/confirm",
        "/api/rounds/messages/{message_id}/confirm",
        "/api/rounds/messages/{message_id}/decline",
    )
    ws_types = set(c.WSMessageType.__args__)
    assert len(ws_types) == 21 and {"state_snapshot", "mode_change", "family_story_sent", "treating_set"} <= ws_types
    for t in sorted(ws_types):
        c.WSMessage(type=t)


def test_settings_defaults_are_the_spec_numbers():
    s = c.Settings()
    assert (s.low_threshold, s.high_threshold, s.predictive_lead_min, s.consecutive_predictions_n) == (70, 250, 30, 2)
    assert (s.night_window_start, s.night_window_end, s.high_alert_mode, s.iob_duration_hours) == ("22:00", "07:00", "oneshot", 4.0)
    assert s.presence_override == "auto" and s.family_recipients == []
    assert s.night_buddy.model_dump() == {"have_buddy": False, "be_watcher": False, "hub_watchable": False, "hub_volunteer": False}
    assert c.WatchOptions().model_dump() == {"ketone_prompts": False, "step_week_vigilance": False, "vigilance_offset_mgdl": 10.0}


def test_snapshot_fixture_is_the_ws_payload_shape():
    payload = json.loads((FIXTURES / "state_snapshot.json").read_text())
    assert set(payload) == {"latest_reading", "forecast", "alarm", "settings", "presence", "mode", "active_plan",
                            "pending_doctor_messages", "todays_checkin_status", "pairing_state", "buddy_state",
                            "family_story_status", "clock_synced"}
