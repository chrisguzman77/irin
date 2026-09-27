"""R14(c) check: Brain versus Bedside. The demo panel's send_card with mode
"both" evaluates the current due card twice through the real engines (the
Step Watch card during a watch, the Basal Check otherwise) and sends both to
the paired demo doctor. The two cards carry the same metric keys and the same
values; they differ only in confidence labels and source; no brain row is
None or missing. brain_only is restored afterwards, even on error, and the
default mode still sends the fixture exactly as before."""

import pytest

from app import main
from app.alarm import PREDICTED_LOW_THRESHOLD_MGDL
from tests.test_seek import H, rig  # noqa: F401  (the fixture: a fresh store, a paired demo doctor, a fake relay)

LABELS = {"measured", "reported", "inferred"}


def _pair(c, rig, body: dict):
    r = c.post("/api/demo/send_card", json=body, headers=H)
    assert r.status_code == 200, r.text
    sent = r.json()["cards"]
    assert [s["status"] for s in sent] == ["sent", "sent"] and all(s["recipients"] == ["doc-8"] for s in sent)
    ids = [s["card_id"] for s in sent]
    assert ids[0].endswith(":irin_bedside") and ids[1].endswith(":irin_brain")
    assert set(ids) <= {e["card_id"] for e in rig.relay.cards}  # both reached the doctor, sealed
    stored = {d["card"]["card_id"]: d["card"] for d in c.get("/api/rounds/cards?limit=500").json()}
    return stored[ids[0]], stored[ids[1]]


def _differ_only_in_labels(bedside: dict, brain: dict) -> None:
    assert bedside["source"] == "irin_bedside" and brain["source"] == "irin_brain"
    assert bedside["metrics"] == brain["metrics"]  # same keys, same values
    assert bedside["confidence"].keys() == brain["confidence"].keys()
    assert all(v is not None for v in brain["metrics"].values())
    assert set(brain["confidence"].values()) <= LABELS and set(bedside["confidence"].values()) <= LABELS
    alarm_rows = _alarm_rows(bedside)
    assert alarm_rows and all(brain["confidence"][k] == "inferred" for k in alarm_rows)  # alarm-derived: inferred
    same = {"card_id", "source", "confidence", "narrative", "generated_at"}
    assert {k: v for k, v in bedside.items() if k not in same} == {k: v for k, v in brain.items() if k not in same}
    assert bedside["kind"] == brain["kind"] and bedside["status"] == brain["status"]


def _alarm_rows(card: dict) -> set[str]:
    return {k for k in card["confidence"] if any(w in k for w in ("near_miss", "escalated", "rearm", "ack"))}


def test_both_during_a_step_watch_sends_the_current_check_twice_differing_only_in_labels(rig):
    c = rig.open()
    try:
        c.post("/api/demo/scenario", json={"name": "titration_synthetic"}, headers=H)
        assert c.post("/api/demo/seek", json={"step": 2, "day": 8}, headers=H).status_code == 200
        bedside, brain = _pair(c, rig, {"mode": "both"})
        assert bedside["program"] == "step_watch" and bedside["kind"] == "step_check" and bedside["step_index"] == 1
        _differ_only_in_labels(bedside, brain)
        assert bedside["confidence"] != brain["confidence"]  # the near-misses: measured at the bedside, inferred by the brain
        assert main.config.IRIN_BRAIN_ONLY is False  # restored
        # the step week of step 2 is over by day 8: the base threshold stands (vigilance is wired, not stuck)
        assert main.runtime.alarm.predicted_low_threshold() == PREDICTED_LOW_THRESHOLD_MGDL
    finally:
        c.__exit__(None, None, None)


def test_both_without_a_watch_sends_the_basal_check_twice(rig):
    c = rig.open()
    try:
        c.post("/api/demo/scenario", json={"name": "basal_change_1"}, headers=H)
        assert c.post("/api/demo/seek", json={"date": "2021-02-15"}, headers=H).status_code == 200
        bedside, brain = _pair(c, rig, {"mode": "both"})
        assert bedside["program"] == "standing" and bedside["kind"] == "basal_check"
        _differ_only_in_labels(bedside, brain)
        assert bedside["confidence"] != brain["confidence"]
    finally:
        c.__exit__(None, None, None)


def test_brain_only_is_restored_on_error_and_bedside_is_the_fixture_as_before(rig, monkeypatch):
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
                c.post("/api/demo/send_card", json={"mode": "brain"}, headers=H)
        assert seen == [True] and main.config.IRIN_BRAIN_ONLY is False
        r = c.post("/api/demo/send_card", json={"fixture": "signal_card_step"}, headers=H)  # the default: bedside
        assert r.status_code == 200 and r.json()["status"] == "sent" and "cards" not in r.json()
        assert c.post("/api/demo/send_card", json={"mode": "sideways"}, headers=H).status_code == 422
    finally:
        c.__exit__(None, None, None)
