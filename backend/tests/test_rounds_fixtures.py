"""R1 check: the card, low event, and recall fixtures handed to Justin validate
against contracts.py and obey the card invariants (7, 9): every metric row
carries a confidence label, no row is blank, every number in the headline
and narrative is a computed metric, no dose recommendation anywhere, and
the metric keys are exactly the ones ml/models/nights.py produces so R8 can
pass George's dicts straight through."""

import json
import re
from pathlib import Path

import pytest

from app import contracts as c
from app.reports import extract_numbers

FIXTURES = Path(__file__).resolve().parent / "fixtures"

STANDING_KEYS = {"nights", "clean_nights", "rise_median_clean", "same_direction_share", "near_misses",
                 "escalated_warnings", "rearms", "median_ack_min", "nocturnal_lows", "answered", "unfelt_lows",
                 "no_answer", "unfelt_low_rate", "inferred_unfelt_unanswered", "excluded_nights"}
STEP_KEYS = {"low_point_shift", "window_low_point", "baseline_low_point", "baseline_nights", "coverage_pct",
             "insufficient", "tbr_pct", "near_misses", "ketone_risk_episodes", "tolerance", "adherence"}
UNLABELED_OK = {"nights", "excluded_nights", "window_low_point", "baseline_low_point", "insufficient"}  # counts and lists
# a dose: insulin units, or mg NOT followed by /dL (42 mg/dL is a glucose value, 5 mg is a drug dose), or advice verbs
DOSE_WORDS = re.compile(r"\b\d+(\.\d+)?\s*(units?|u|iu)\b|\b\d+(\.\d+)?\s*mg\b(?!\s*/\s*dl)"
                        r"|\bincrease\b|\bdecrease\b|reduce your|take \d", re.IGNORECASE)
PLAN_LABEL = "5 mg"  # the plan's own step label may appear on a step card ("Step 2 (5 mg)"); it is not a recommendation


def load(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


@pytest.fixture(params=["signal_card_standing", "signal_card_step"])
def card(request):
    return c.SignalCard.model_validate(load(request.param))


def _numbers_in(value) -> set[str]:
    out = set()
    if isinstance(value, bool) or value is None:
        return out
    if isinstance(value, (int, float)):
        out |= {f"{value:g}", f"{value:.1f}", f"{round(value)}", f"{abs(value):g}", f"{abs(value):.1f}"}
        if isinstance(value, float) and 0 <= value <= 1:
            out |= {f"{value * 100:g}", f"{value * 100:.1f}"}
    elif isinstance(value, dict):
        for v in value.values():
            out |= _numbers_in(v)
    elif isinstance(value, list):
        out.add(str(len(value)))
        for v in value:
            out |= _numbers_in(v)
    return out


def test_fixtures_validate_and_round_trip():
    for name, model in [("signal_card_standing", c.SignalCard), ("signal_card_step", c.SignalCard),
                        ("low_event", c.LowEvent), ("low_event_recall", c.LowEventRecall)]:
        obj = model.model_validate(load(name))
        assert model.model_validate_json(obj.model_dump_json()) == obj
        assert obj.is_demo is True  # every fixture is SYNTHETIC and badged


def test_every_metric_row_is_labeled_and_never_blank(card):
    for key, value in card.metrics.items():
        assert value is not None, f"{key} is blank: a card never shows a blank row"
        if key not in UNLABELED_OK:
            assert card.confidence.get(key) in ("measured", "reported", "inferred"), f"{key} has no confidence label"
    assert set(card.confidence) <= set(card.metrics), "a label without a row"
    for night in card.nights:
        assert None not in night.values() and night["code_source"] in ("logged", "inferred")


def test_metric_keys_are_georges(card):
    expected = STANDING_KEYS if card.program == "standing" else STEP_KEYS
    assert expected <= set(card.metrics), sorted(expected - set(card.metrics))


def test_headline_and_narrative_numbers_are_computed(card):
    allowed = _numbers_in(card.metrics) | _numbers_in(card.excluded_counts) | {"70", "54", "180"}
    if card.step_index is not None:
        allowed |= _numbers_in(card.tolerance_days) | {str(card.step_index + 1)}  # "Step 2", "3 of 5 days"
        allowed |= {"3", "7"}  # "days 3 to 7": the window's day numbers within the step
    for text in (card.headline, card.narrative):
        for tok in extract_numbers(text.replace(PLAN_LABEL, "")):
            assert tok in allowed, f"{tok!r} in {text!r} is not a computed metric"


def test_no_dose_recommendation_anywhere(card):
    for text in (card.headline, card.narrative, *card.allowed_actions, *card.resource_categories):
        assert not DOSE_WORDS.search(text.replace(PLAN_LABEL, "")), text
    assert all(re.fullmatch(r"[a-z_]+", a) for a in card.allowed_actions)  # verbs the doctor picks, never numbers


def test_card_identity_and_program_consistency(card):
    parts = card.card_id.split(":")
    assert parts[0] == card.program and parts[1] == card.kind
    if card.program == "standing":
        assert card.plan_id is None and card.step_index is None
        assert parts[2:] == [card.period_start.isoformat(), card.period_end.isoformat()]
        assert card.excluded_counts and len(card.metrics["excluded_nights"]) == sum(card.excluded_counts.values())
        assert card.metrics["clean_nights"] + len(card.metrics["excluded_nights"]) == card.metrics["nights"]
    else:
        assert card.plan_id and card.step_index is not None
        assert parts[2:4] == [card.plan_id, str(card.step_index)]
        assert sum(card.metrics["tolerance"].values()) == len(card.tolerance_days)
