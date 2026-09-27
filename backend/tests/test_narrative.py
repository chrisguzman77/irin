"""R13: narrative.py's chain and validator, with fake transports. conftest forces
template mode and blank keys; the tests here replace the three link functions,
so nothing ever reaches the network."""

import asyncio

import pytest

from app.config import config
from app.contracts import NarrativeScope
from app.rounds import narrative

CARD_METRICS = {"clean_nights": 8, "rising_nights": 6, "median_rise_mgdl": 42, "coverage_pct": 91.5}
CARD_CONTEXT = {"kind": "basal_check", "headline": "Overnight glucose rose on 6 of 8 clean nights.",
                "confidence": {"clean_nights": "measured"}, "scope": {"kind": "doctor", "scope_id": "d1"}}
REPORT = {"readings": 100, "coverage_pct": 92.0, "low_mgdl": 64, "low_at": "03:12", "high_mgdl": 188,
          "high_at": "23:40", "tir_pct": 81, "tbr_pct": 4, "tar_pct": 15, "minutes_below_70": 25, "carbs_g": 15,
          "insulin_units": 7.5, "low_point": 61, "nights": [{"rise": 33, "readings": 90}]}
CLEAN = '{"invented_number": false, "advice": false}'
ROUTING = {"clinician_card": ["anthropic", "claude-sonnet-5"], "morning_report": ["anthropic", "claude-sonnet-5"],
           "family_story": ["meta", "muse-spark-1.3"], "buddy_line": ["openrouter", "meta/muse-spark"],
           "match_explanation": ["meta", "muse-spark-1.3"], "second_opinion": ["openai", "gpt-5-mini"]}


class Fakes:
    """Scripted replies per link; an Exception instance is raised; a list is
    consumed in order. `<link>_opinion` scripts the second-opinion call. Records every call."""

    def __init__(self, monkeypatch, **replies):
        self.replies, self.calls = replies, []
        monkeypatch.setattr(narrative, "_call_backboard", self._make("backboard"))
        monkeypatch.setattr(narrative, "_call_meta", self._make("meta"))
        monkeypatch.setattr(narrative, "_call_anthropic", self._make("anthropic"))

    def _make(self, name):
        def call(system, prompt, **kw):
            opinion = "invented_number" in system
            self.calls.append((name + ("_opinion" if opinion else ""), prompt, kw))
            reply = self.replies.get(name, RuntimeError("not scripted"))
            if opinion:
                reply = self.replies.get(f"{name}_opinion", RuntimeError("not scripted"))
            if isinstance(reply, list):
                reply = reply.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply
        return call

    def names(self):
        return [c[0] for c in self.calls]


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(config, "NARRATIVE_BACKEND", "backboard")
    monkeypatch.setattr(config, "NARRATIVE_ROUTING", ROUTING)


def template(task, ctx, metrics):
    return narrative._template(task, ctx, metrics)


# --- the validator ---


def test_validator_matching_text_passes():
    assert narrative.validate("Rose on 6 of 8 clean nights, median +42 mg/dL; coverage 91.5%.", CARD_METRICS)


def test_validator_invented_number_fails():
    assert not narrative.validate("Rose on 7 of 8 clean nights.", CARD_METRICS)
    assert not narrative.validate("Median rise 43 mg/dL.", CARD_METRICS)


def test_validator_forms():
    m = {"low_at": "03:12", "drop": -22, "share": 12.45, "n": 1440, "flag": True, "t": "12:30:45"}
    assert narrative.validate("Lowest at 3:12, and 03:12 again.", m)
    assert narrative.validate("Down 22 from baseline, 12.5% and 12%, 1,440 readings.", m)  # half up: 12.45 -> 12.5
    assert narrative.validate("At 12:30:45, or 12:30.", m)
    assert narrative.validate("Below 70 and above 180.", {})  # the range thresholds
    assert narrative.validate("Range 70,180.", {})  # two numbers, not 70180
    assert not narrative.validate("6,8 nights.", {"a": 6})  # the 8 after a comma is still a token
    assert not narrative.validate("Lowest at 3:13.", m)
    assert not narrative.validate("1 flag.", m)  # a bool is never a number
    assert not narrative.validate("At 3am.", m) and not narrative.validate("42mg/dL", m)
    assert narrative.validate("Nested 5.", {"nights": [{"n": 5}]})


def test_validator_domain_terms_are_not_numbers():
    assert narrative.validate("An adult with type 1 diabetes (T1D) on a GLP-1, Level 2 story.", {})
    assert narrative.validate("Level 1 only.", {})
    assert narrative.validate("Step 2 check.", {"step_index": 2})
    assert not narrative.validate("Step 3 check.", {"step_index": 2})
    assert not narrative.validate("type 2 in 9 nights", {})


def test_validator_signs():
    assert narrative.validate("Down -22 from baseline.", {"delta": -22})
    assert not narrative.validate("Up +22 from baseline.", {"delta": -22})
    assert not narrative.validate("Rose 22 from baseline.", {"delta": -22})
    assert narrative.validate("Rose +42.", {"rise": 42}) and narrative.validate("Fell 22.", {"delta": -22})
    assert narrative.validate("Rose −22.", {"delta": -22})  # the unicode minus is a sign


# --- the chain ---


def test_template_mode_calls_nothing(monkeypatch):
    fakes = Fakes(monkeypatch, backboard="x", meta="x", anthropic="x")
    text = narrative.generate("card", CARD_CONTEXT, CARD_METRICS)
    assert fakes.calls == []
    assert text.startswith(CARD_CONTEXT["headline"])


def test_invented_number_ships_the_template(monkeypatch, live):
    Fakes(monkeypatch, backboard="Rose on 7 of 8 clean nights.", backboard_opinion=CLEAN)
    assert narrative.generate("card", CARD_CONTEXT, CARD_METRICS) == template("card", CARD_CONTEXT, CARD_METRICS)


def test_matching_card_passes_with_a_clean_second_opinion(monkeypatch, live):
    good = "Overnight glucose rose on 6 of 8 clean nights, by a median of 42 mg/dL."
    fakes = Fakes(monkeypatch, backboard=good, backboard_opinion=CLEAN)
    assert narrative.generate("card", CARD_CONTEXT, CARD_METRICS) == good
    opinion = fakes.calls[-1]
    assert opinion[0] == "backboard_opinion"
    assert opinion[2]["provider"] == "openai" and opinion[2]["scope"] is None and good in opinion[1]


@pytest.mark.parametrize("opinion", ['{"invented_number": false, "advice": true}',
                                     '{"invented_number": true, "advice": false}',
                                     "not json", RuntimeError("down")])
def test_second_opinion_fails_closed(monkeypatch, live, opinion):
    Fakes(monkeypatch, backboard="Rose on 6 of 8 clean nights.", backboard_opinion=opinion)
    assert narrative.generate("card", CARD_CONTEXT, CARD_METRICS) == template("card", CARD_CONTEXT, CARD_METRICS)


def test_second_opinion_never_from_the_writers_family(monkeypatch, live):
    monkeypatch.setattr(config, "NARRATIVE_ROUTING", {**ROUTING, "second_opinion": ["anthropic", "claude-haiku-4-5"]})
    Fakes(monkeypatch, backboard="Rose on 6 of 8 clean nights.", anthropic_opinion=CLEAN)
    assert narrative.generate("card", CARD_CONTEXT, CARD_METRICS) == template("card", CARD_CONTEXT, CARD_METRICS)
    # the same family through another provider name is still the same family
    monkeypatch.setattr(config, "NARRATIVE_ROUTING", {**ROUTING, "second_opinion": ["openrouter", "anthropic/claude-haiku"]})
    fakes = Fakes(monkeypatch, backboard="Rose on 6 of 8 clean nights.", backboard_opinion=CLEAN)
    assert narrative.generate("card", CARD_CONTEXT, CARD_METRICS) == template("card", CARD_CONTEXT, CARD_METRICS)
    assert "backboard_opinion" not in fakes.names()


def test_card_without_a_second_opinion_row_ships_the_template(monkeypatch, live):
    monkeypatch.setattr(config, "NARRATIVE_ROUTING", {k: v for k, v in ROUTING.items() if k != "second_opinion"})
    Fakes(monkeypatch, backboard="Rose on 6 of 8 clean nights.")
    assert narrative.generate("card", CARD_CONTEXT, CARD_METRICS) == template("card", CARD_CONTEXT, CARD_METRICS)


@pytest.mark.parametrize("text", ["Rose on 6 of 8 clean nights; the 8 units look right.",
                                  "Rose on 6 of 8 clean nights on the current dose.",
                                  "Rose on 6 of 8 clean nights; basal 18 overnight."])
def test_card_never_names_a_dose(monkeypatch, live, text):
    metrics = {**CARD_METRICS, "basal_insulin_units": 18}
    Fakes(monkeypatch, backboard=text, backboard_opinion=CLEAN)
    assert narrative.generate("card", CARD_CONTEXT, metrics) == template("card", CARD_CONTEXT, metrics)


def test_each_link_degrades_to_the_next_in_order(monkeypatch, live):
    # openrouter row: Backboard -> Meta direct -> Anthropic -> template
    fakes = Fakes(monkeypatch, backboard=TimeoutError(), meta=TimeoutError(), anthropic="All quiet last night.")
    assert narrative.generate("buddy_line", {"kind": "all_quiet"}, {}) == "All quiet last night."
    assert fakes.names() == ["backboard", "meta", "anthropic"]

    fakes = Fakes(monkeypatch, backboard=TimeoutError(), meta=TimeoutError(), anthropic=RuntimeError("no key"))
    assert narrative.generate("buddy_line", {"kind": "all_quiet"}, {}) == template("buddy_line", {"kind": "all_quiet"}, {})
    assert fakes.names() == ["backboard", "meta", "anthropic"]


def test_an_empty_reply_falls_to_the_next_link(monkeypatch, live):
    fakes = Fakes(monkeypatch, backboard="  ", anthropic="Lowest 64 mg/dL at 3:12.")
    assert narrative.generate("morning_report", {}, REPORT) == "Lowest 64 mg/dL at 3:12."
    assert fakes.names() == ["backboard", "anthropic"]


def test_one_deadline_covers_the_chain(monkeypatch, live):
    clock = [0.0]
    monkeypatch.setattr(narrative, "_now", lambda: clock[0])

    def slow(*a, **kw):
        clock[0] += 19.5  # the first link eats nearly the whole 20 s
        raise TimeoutError()

    fakes = Fakes(monkeypatch, anthropic="Lowest 64 mg/dL at 3:12.")
    monkeypatch.setattr(narrative, "_call_backboard", slow)
    assert narrative.generate("morning_report", {}, REPORT) == template("morning_report", {}, REPORT)
    assert fakes.names() == []  # 0.5 s left: Anthropic is never started

    clock[0] = 0.0
    seen = []
    monkeypatch.setattr(narrative, "_call_backboard", lambda *a, **kw: (clock.__setitem__(0, 5.0), seen.append(kw["timeout"]))
                        and (_ for _ in ()).throw(TimeoutError()))
    monkeypatch.setattr(narrative, "_call_anthropic", lambda *a, **kw: seen.append(kw["timeout"]) or "Lowest 64 mg/dL.")
    narrative.generate("morning_report", {}, REPORT)
    assert seen == [20.0, 15.0]  # each link gets what is left, not a fresh 20 s


def test_anthropic_row_skips_meta(monkeypatch, live):
    fakes = Fakes(monkeypatch, backboard=RuntimeError("down"), anthropic="Lowest 64 mg/dL at 3:12.")
    assert narrative.generate("morning_report", {}, REPORT) == "Lowest 64 mg/dL at 3:12."
    assert fakes.names() == ["backboard", "anthropic"]


def test_backend_anthropic_starts_at_anthropic(monkeypatch, live):
    monkeypatch.setattr(config, "NARRATIVE_BACKEND", "anthropic")
    fakes = Fakes(monkeypatch, backboard="x", anthropic="Lowest 64 mg/dL at 3:12.")
    narrative.generate("morning_report", {}, REPORT)
    assert fakes.names() == ["anthropic"]


def test_meta_row_reaches_meta_never_backboard_and_no_glucose_or_insulin(monkeypatch, live):
    story = "Last night had a low around 3:12; it was caught and treated."
    fakes = Fakes(monkeypatch, backboard="x", meta=story)
    ctx = {"level": "story_and_view", "name": "Ana"}
    assert narrative.generate("family_story", ctx, REPORT) == story
    assert fakes.names() == ["meta"]
    prompt = fakes.calls[0][1]
    for banned in ("64", "188", "61", "33", "7.5", "insulin", "mgdl", "low_point", "rise"):
        assert banned not in prompt
    assert "03:12" in prompt and "tir_pct" in prompt


def test_a_muse_model_on_backboard_is_scrubbed_with_memory_off(monkeypatch, live):
    fakes = Fakes(monkeypatch, backboard="All quiet last night.")
    metrics = {"call_at": "03:12", "nadir_mgdl": 58, "insulin_units": 4}
    narrative.generate("buddy_line", {"kind": "all_quiet", "scope": {"kind": "buddy", "scope_id": "b1"}}, metrics)
    name, prompt, kw = fakes.calls[0]
    assert name == "backboard" and kw["scope"] is None
    assert "58" not in prompt and "insulin" not in prompt and "03:12" in prompt


def test_muse_output_with_a_glucose_value_ships_the_template(monkeypatch, live):
    Fakes(monkeypatch, meta="Down to 64 mg/dL at 3:12.")
    ctx = {"level": "story_and_view", "name": "Ana"}
    assert narrative.generate("family_story", ctx, REPORT) == template("family_story", ctx, REPORT)


def test_spelled_out_numbers_ship_the_template(monkeypatch, live):
    Fakes(monkeypatch, backboard="Lowest at 3:12, about twenty minutes low.")
    assert narrative.generate("morning_report", {}, REPORT) == template("morning_report", {}, REPORT)


def test_no_data_morning_is_always_the_template(monkeypatch, live):
    empty = {**REPORT, "readings": 0}
    fakes = Fakes(monkeypatch, backboard="Irin didn't have data; we define nothing.")
    assert narrative.generate("morning_report", {}, empty) == template("morning_report", {}, empty)
    assert fakes.calls == []


def test_on_the_event_loop_the_template_ships(monkeypatch, live):
    fakes = Fakes(monkeypatch, anthropic="Lowest 64 mg/dL at 3:12.")

    async def inside():
        return narrative.generate("morning_report", {}, REPORT)

    assert asyncio.run(inside()) == template("morning_report", {}, REPORT)
    assert fakes.calls == []


def test_buddy_templates():
    close = narrative._buddy_line_template({"kind": "close_out", "name": "Sam", "treated": True, "recovered": True},
                                           {"call_at": "03:12"})
    assert close == "Sam's okay. Your call at 3:12 got through. Sam treated and recovered."
    assert narrative.validate(close, {"call_at": "03:12"})
    intro = narrative._buddy_intro_template({"name": "Lee", "shared_languages": ["English"]}, {"hours_covered": 6})
    assert "Lee" in intro and "6 hours" in intro and narrative.validate(intro, {"hours_covered": 6})
    assert "6.5 hours" in narrative._buddy_intro_template({}, {"hours_covered": "6.5"})  # a string never raises


def _fake_backboard(monkeypatch):
    import backboard

    sent, created = [], []

    class Resp:
        content, thread_id = "ok", "t-1"

    class Assistant:
        assistant_id = "a-1"

    class FakeClient:
        def __init__(self, api_key, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def create_assistant(self, name, **kw):
            created.append(name)
            return Assistant()

        async def send_message(self, **kw):
            sent.append(kw)
            return Resp()

    monkeypatch.setattr(backboard, "BackboardClient", FakeClient)
    monkeypatch.setattr(config, "BACKBOARD_API_KEY", "test-key")
    from app import store

    store.init_db()
    return sent, created


def test_backboard_one_assistant_and_thread_per_scope(monkeypatch):
    """The real _call_backboard with the SDK client faked: the first call creates
    the assistant and stores both ids; the second reuses them; family memory is Readonly."""
    sent, created = _fake_backboard(monkeypatch)
    scope = NarrativeScope(kind="family", scope_id="r1")
    narrative._call_backboard("s", "p", provider="anthropic", model="m", scope=scope, timeout=20.0)
    narrative._call_backboard("s", "p", provider="anthropic", model="m", scope=scope, timeout=20.0)
    assert created == ["irin-family"]
    assert sent[0]["assistant_id"] == "a-1" and sent[0]["thread_id"] is None
    assert sent[1]["assistant_id"] == "a-1" and sent[1]["thread_id"] == "t-1"
    assert sent[0]["memory"] == "Readonly"


def test_no_scope_means_memory_off(monkeypatch):
    sent, created = _fake_backboard(monkeypatch)
    narrative._call_backboard("s", "p", provider="anthropic", model="m", scope=None, timeout=20.0)
    assert created == [] and sent[0]["memory"] == "off" and sent[0]["assistant_id"] is None
    assert narrative._scope("card", {}) is None and narrative._scope("buddy_line", {}) is None
    assert narrative._scope("buddy_intro", {}) is None
    assert narrative._scope("morning_report", {}).kind == "patient"


def test_second_opinion_prompt_loads():
    system, user = narrative._second_opinion_prompt()
    assert "invented_number" in system and "{narrative_text}" in user


def test_card_echoing_the_headline_dose_ships_the_template_and_step_n_passes(monkeypatch, live):
    """The Step Watch headline shows the dose ("Step 2 (5 mg)"); the narrative never repeats it."""
    ctx = {**CARD_CONTEXT, "kind": "step_check", "headline": "Step 2 (5 mg), days 3 to 7."}
    metrics = {**CARD_METRICS, "step_index": 2, "headline_numbers": [5.0, 3.0, 7.0]}
    Fakes(monkeypatch, backboard="During Step 2 (5 mg), overnight glucose rose on 6 of 8 clean nights.",
          backboard_opinion=CLEAN)
    assert narrative.generate("card", ctx, metrics) == template("card", ctx, metrics)
    good = "During step 2, overnight glucose rose on 6 of 8 clean nights."
    Fakes(monkeypatch, backboard=good, backboard_opinion=CLEAN)
    assert narrative.generate("card", ctx, metrics) == good


def test_card_prompt_forbids_any_dose_even_the_headlines():
    s = narrative.CARD_SYSTEM.lower()
    assert "drug strength" in s and "even" in s and "headline" in s and "this step" in s


# --- buddy v3: match_why ---

WHY_CTX = {"name": "Sam", "shared_languages": ["English", "Japanese"], "mirror": True}
WHY_METRICS = {"hours_covered": 8, "shared_language_count": 2}


def test_match_why_template_mode_calls_nothing_and_is_the_why_line(monkeypatch):
    from app.buddy.directory import why_line
    fakes = Fakes(monkeypatch, backboard="x", meta="x", anthropic="x")
    out = narrative.generate("match_why", WHY_CTX, WHY_METRICS)
    assert out == why_line(8, True, ["English", "Japanese"]) and fakes.calls == []


def test_match_why_is_routed_like_match_explanation_and_validated(monkeypatch, live):
    fakes = Fakes(monkeypatch, backboard="x", meta="Sam is awake for 8 of your night hours and shares 2 languages.")
    assert narrative.generate("match_why", WHY_CTX, WHY_METRICS) == \
        "Sam is awake for 8 of your night hours and shares 2 languages."
    assert fakes.names() == ["meta"]  # Muse, never Backboard
    Fakes(monkeypatch, meta="Sam is awake for 9 of your night hours.")
    assert narrative.generate("match_why", WHY_CTX, WHY_METRICS) == template("match_why", WHY_CTX, WHY_METRICS)


def test_buddy_text_may_not_name_a_glucose_threshold():
    """Review: 70/54/180 are allowed on cards but never in a buddy's words (invariant 15)."""
    assert narrative.validate("keeps you above 70 overnight", {"hours_covered": 8})
    assert not narrative.validate("keeps you above 70 overnight", {"hours_covered": 8}, thresholds=False)
    assert not narrative._passes("match_why", {}, "Sam keeps you above 70 overnight.", {"hours_covered": 8})
