"""R13: narrative.py's chain and validator, with fake transports. conftest forces
template mode and blank keys; the tests here replace the three link functions,
so nothing ever reaches the network."""

import pytest

from app.config import config
from app.rounds import narrative

CARD_METRICS = {"clean_nights": 8, "rising_nights": 6, "median_rise_mgdl": 42, "coverage_pct": 91.5}
CARD_CONTEXT = {"kind": "basal_check", "headline": "Overnight glucose rose on 6 of 8 clean nights.",
                "confidence": {"clean_nights": "measured"}}
REPORT = {"readings": 100, "coverage_pct": 92.0, "low_mgdl": 64, "low_at": "03:12", "high_mgdl": 188,
          "high_at": "23:40", "tir_pct": 81, "tbr_pct": 4, "tar_pct": 15, "minutes_below_70": 25, "carbs_g": 15}
CLEAN = '{"invented_number": false, "advice": false}'
ROUTING = {"clinician_card": ["anthropic", "claude-sonnet-5"], "morning_report": ["anthropic", "claude-sonnet-5"],
           "family_story": ["meta", "muse-spark-1.3"], "buddy_line": ["openrouter", "meta/muse-spark"],
           "match_explanation": ["meta", "muse-spark-1.3"], "second_opinion": ["openai", "gpt-5-mini"]}


class Fakes:
    """Scripted replies per link; an Exception instance is raised. Records every call."""

    def __init__(self, monkeypatch, **replies):
        self.replies, self.calls = replies, []
        monkeypatch.setattr(narrative, "_call_backboard", self._make("backboard"))
        monkeypatch.setattr(narrative, "_call_meta", self._make("meta"))
        monkeypatch.setattr(narrative, "_call_anthropic", self._make("anthropic"))

    def _make(self, name):
        def call(system, prompt, **kw):
            self.calls.append((name, prompt, kw))
            reply = self.replies.get(name, RuntimeError("not scripted"))
            if kw.get("json_output") or kw.get("scope", "x") is None:  # the second opinion
                reply = self.replies.get(f"{name}_opinion", reply)
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


# --- the validator ---


def test_validator_matching_text_passes():
    assert narrative.validate("Rose on 6 of 8 clean nights, median +42 mg/dL; coverage 91.5%.", CARD_METRICS)


def test_validator_invented_number_fails():
    assert not narrative.validate("Rose on 7 of 8 clean nights.", CARD_METRICS)
    assert not narrative.validate("Median rise 43 mg/dL.", CARD_METRICS)


def test_validator_forms():
    m = {"low_at": "03:12", "drop": -22, "share": 12.46, "n": 1440, "flag": True}
    assert narrative.validate("Lowest at 3:12, and 03:12 again.", m)
    assert narrative.validate("Down 22 from baseline, 12.5% and 12%, 1,440 readings.", m)
    assert narrative.validate("Below 70 and above 180.", {})  # the range thresholds
    assert not narrative.validate("Lowest at 3:13.", m)
    assert not narrative.validate("1 flag.", m)  # a bool is never a number
    assert narrative.validate("Nested 5.", {"nights": [{"n": 5}]})


# --- the chain ---


def test_template_mode_calls_nothing(monkeypatch):
    fakes = Fakes(monkeypatch, backboard="x", meta="x", anthropic="x")
    text = narrative.generate("card", CARD_CONTEXT, CARD_METRICS)
    assert fakes.calls == []
    assert text.startswith(CARD_CONTEXT["headline"])


def test_invented_number_ships_the_template(monkeypatch, live):
    Fakes(monkeypatch, backboard="Rose on 7 of 8 clean nights.", backboard_opinion=CLEAN)
    assert narrative.generate("card", CARD_CONTEXT, CARD_METRICS) == narrative._template("card", CARD_CONTEXT, CARD_METRICS)


def test_matching_card_passes_with_a_clean_second_opinion(monkeypatch, live):
    good = "Overnight glucose rose on 6 of 8 clean nights, by a median of 42 mg/dL."
    fakes = Fakes(monkeypatch, backboard=good, backboard_opinion=CLEAN)
    assert narrative.generate("card", CARD_CONTEXT, CARD_METRICS) == good
    opinion = fakes.calls[-1]
    assert opinion[2]["provider"] == "openai" and opinion[2]["scope"] is None and good in opinion[1]


@pytest.mark.parametrize("opinion", ['{"invented_number": false, "advice": true}',
                                     '{"invented_number": true, "advice": false}',
                                     "not json", RuntimeError("down")])
def test_second_opinion_fails_closed(monkeypatch, live, opinion):
    Fakes(monkeypatch, backboard="Rose on 6 of 8 clean nights.", backboard_opinion=opinion)
    assert narrative.generate("card", CARD_CONTEXT, CARD_METRICS) == narrative._template("card", CARD_CONTEXT, CARD_METRICS)


def test_second_opinion_never_from_the_writers_provider(monkeypatch, live):
    monkeypatch.setattr(config, "NARRATIVE_ROUTING", {**ROUTING, "second_opinion": ["anthropic", "claude-haiku-4-5"]})
    Fakes(monkeypatch, backboard="Rose on 6 of 8 clean nights.", anthropic=CLEAN)
    assert narrative.generate("card", CARD_CONTEXT, CARD_METRICS) == narrative._template("card", CARD_CONTEXT, CARD_METRICS)


def test_each_link_degrades_to_the_next_in_order(monkeypatch, live):
    # openrouter row: Backboard -> Meta direct -> Anthropic -> template
    fakes = Fakes(monkeypatch, backboard=TimeoutError(), meta=TimeoutError(), anthropic="All quiet last night.")
    assert narrative.generate("buddy_line", {"kind": "all_quiet"}, {}) == "All quiet last night."
    assert fakes.names() == ["backboard", "meta", "anthropic"]

    fakes = Fakes(monkeypatch, backboard=TimeoutError(), meta=TimeoutError(), anthropic=RuntimeError("no key"))
    assert narrative.generate("buddy_line", {"kind": "all_quiet"}, {}) == narrative._buddy_line_template({"kind": "all_quiet"}, {})
    assert fakes.names() == ["backboard", "meta", "anthropic"]


def test_anthropic_row_skips_meta(monkeypatch, live):
    fakes = Fakes(monkeypatch, backboard=RuntimeError("down"), anthropic="Lowest 64 mg/dL at 3:12.")
    assert narrative.generate("morning_report", {}, REPORT) == "Lowest 64 mg/dL at 3:12."
    assert fakes.names() == ["backboard", "anthropic"]


def test_backend_anthropic_starts_at_anthropic(monkeypatch, live):
    monkeypatch.setattr(config, "NARRATIVE_BACKEND", "anthropic")
    fakes = Fakes(monkeypatch, backboard="x", anthropic="Lowest 64 mg/dL at 3:12.")
    narrative.generate("morning_report", {}, REPORT)
    assert fakes.names() == ["anthropic"]


def test_meta_row_reaches_meta_never_backboard_and_no_glucose(monkeypatch, live):
    story = "Last night had a low around 3:12; it was caught and treated."
    fakes = Fakes(monkeypatch, backboard="x", meta=story)
    ctx = {"level": "story_only", "name": "Ana"}
    assert narrative.generate("family_story", ctx, REPORT) == story
    assert fakes.names() == ["meta"]
    prompt = fakes.calls[0][1]
    assert "low_mgdl" not in prompt and "188" not in prompt and "64" not in prompt


def test_muse_output_with_a_glucose_value_ships_the_template(monkeypatch, live):
    Fakes(monkeypatch, meta="Down to 64 mg/dL at 3:12, then fine.")
    ctx = {"level": "story_and_view", "name": "Ana"}
    assert narrative.generate("family_story", ctx, REPORT) == narrative._template("family_story", ctx, REPORT)


def test_no_data_morning_is_never_fine(monkeypatch, live):
    empty = {**REPORT, "readings": 0}
    Fakes(monkeypatch, backboard="The night was fine.")
    assert narrative.generate("morning_report", {}, empty) == narrative._template("morning_report", {}, empty)


def test_buddy_templates():
    close = narrative._buddy_line_template({"kind": "close_out", "name": "Sam", "treated": True, "recovered": True},
                                           {"call_at": "03:12"})
    assert close == "Sam's okay. Your call at 3:12 got through. Sam treated and recovered."
    assert narrative.validate(close, {"call_at": "03:12"})
    intro = narrative._buddy_intro_template({"name": "Lee", "shared_languages": ["English"]}, {"hours_covered": 6})
    assert "Lee" in intro and "6 hours" in intro


def test_backboard_scope_thread_is_kept(monkeypatch, live):
    """The real _call_backboard with the SDK client faked: the first call stores
    the thread, the second sends it back; family memory is Readonly."""
    import backboard

    sent = []

    class Resp:
        content, thread_id, assistant_id = "ok", "t-1", "a-1"

    class FakeClient:
        def __init__(self, api_key, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def send_message(self, **kw):
            sent.append(kw)
            return Resp()

    monkeypatch.setattr(backboard, "BackboardClient", FakeClient)
    monkeypatch.setattr(config, "BACKBOARD_API_KEY", "test-key")
    from app import store
    from app.contracts import NarrativeScope

    store.init_db()
    scope = NarrativeScope(kind="family", scope_id="r1")
    narrative._call_backboard("s", "p", provider="anthropic", model="m", scope=scope)
    narrative._call_backboard("s", "p", provider="anthropic", model="m", scope=scope)
    assert sent[0]["thread_id"] is None and sent[1]["thread_id"] == "t-1"
    assert sent[0]["memory"] == "Readonly"


def test_second_opinion_prompt_loads():
    system, user = narrative._second_opinion_prompt()
    assert "invented_number" in system and "{narrative_text}" in user
