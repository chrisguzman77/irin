"""R13 check: a card names resource CATEGORIES only, from one table; a Hypo
Response offers glucagon access, an early check the access and refill
categories, a gate the next pen strength, a rough stomach the GI education, a
red safety card the MSL; Basal Check, Follow-up and graduation offer none;
never a brand, never a number; the relay's category tokens are the same."""

import re
from pathlib import Path

from app.rounds.resources import BANNER, CATEGORIES, categories_for


def test_categories_by_kind_and_flag():
    assert categories_for("hypo_response", ["awareness"]) == ["glucagon_access"]
    assert categories_for("early_check", []) == ["copay_savings", "prior_auth_hub", "bridge_supply"]
    assert categories_for("early_check", ["tolerance"]) == ["copay_savings", "prior_auth_hub", "bridge_supply", "gi_side_effect_education"]
    assert categories_for("step_check", ["lows", "tolerance"]) == ["gi_side_effect_education"]
    assert categories_for("step_check", ["lows"]) == []
    assert categories_for("step_gate", ["tolerance"]) == ["samples_next_pen", "gi_side_effect_education"]
    assert categories_for("safety", ["level2"]) == ["ask_msl"]
    for kind in ("basal_check", "follow_up", "graduation", "baseline_note"):
        assert categories_for(kind, ["tolerance", "lows"]) == []
    assert all(c in CATEGORIES for k in ("hypo_response", "early_check", "step_gate", "safety") for c in categories_for(k, ["tolerance"]))


def test_the_relay_and_the_device_agree_on_the_tokens_and_the_banner():
    relay = (Path(__file__).resolve().parents[2] / "relay" / "relay_api.py").read_text()
    m = re.search(r"RESOURCE_CATEGORIES = \((.*?)\)", relay, re.S)
    assert m and tuple(re.findall(r'"([a-z_]+)"', m.group(1))) == CATEGORIES
    assert f'RESOURCE_BANNER = "{BANNER}"' in relay
    readme = (Path(__file__).resolve().parents[2] / "relay" / "README.md").read_text()
    assert all(c in readme for c in CATEGORIES) and BANNER in readme


def test_engine_cards_carry_their_categories():
    """Through the two engines' assemble calls: the categories come from the table, never typed by hand."""
    import inspect

    from app.rounds import evaluate, step_watch

    assert "categories_for(ev.kind, ev.flags)" in inspect.getsource(evaluate) and "categories_for(ev.kind, ev.flags)" in inspect.getsource(step_watch)
    for mod in (evaluate, step_watch):
        assert '"glucagon' not in inspect.getsource(mod) and "gi_side_effect" not in inspect.getsource(mod)
