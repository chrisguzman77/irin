"""The default NARRATIVE_ROUTING (config.default_routing), used only when the
env var is unset: Muse Spark writes family_story, buddy_line, and buddy_intro
only when META_MODEL_API_KEY is set; cards and the morning report stay on
Claude; the second opinion is never the writer's family. Link functions are
faked (tests/test_narrative.py's Fakes): nothing reaches the network."""

import pytest

from app import config as config_mod
from app.config import config
from app.reports import DEFAULT_MODEL
from app.rounds import narrative
from tests.test_narrative import REPORT, Fakes

MUSE_TASKS = ("family_story", "buddy_line", "buddy_intro")


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(config_mod, "_load_dotenv", lambda *a, **k: None)  # the environment only, never .env
    monkeypatch.delenv("NARRATIVE_ROUTING", raising=False)
    monkeypatch.delenv("META_MODEL_API_KEY", raising=False)
    return monkeypatch


def test_without_a_meta_key_the_muse_tasks_stay_where_they_are_today(env):
    routing = config_mod.load_config().NARRATIVE_ROUTING
    assert not any(row[0] == "meta" for row in routing.values())
    env.setattr(config, "NARRATIVE_ROUTING", routing)
    for task in MUSE_TASKS:
        assert narrative._route(task) == ("anthropic", DEFAULT_MODEL)


def test_with_a_meta_key_muse_writes_the_three_and_claude_the_rest(env):
    env.setenv("META_MODEL_API_KEY", "test-key")
    routing = config_mod.load_config().NARRATIVE_ROUTING
    env.setattr(config, "NARRATIVE_ROUTING", routing)
    for task in MUSE_TASKS:
        assert narrative._route(task) == ("meta", narrative.META_MODEL)
    assert narrative._route("card") == narrative._route("morning_report") == ("anthropic", DEFAULT_MODEL)
    checker = tuple(routing["second_opinion"])
    assert not narrative._is_muse(*checker)
    for task in ("card", "morning_report", *MUSE_TASKS):
        assert narrative._family(*checker) != narrative._family(*narrative._route(task))


def test_an_explicit_routing_env_var_is_used_verbatim(env):
    env.setenv("META_MODEL_API_KEY", "test-key")
    env.setenv("NARRATIVE_ROUTING", '{"family_story": ["anthropic", "claude-x"]}')
    assert config_mod.load_config().NARRATIVE_ROUTING == {"family_story": ["anthropic", "claude-x"]}


@pytest.mark.parametrize("backend", ["anthropic", "backboard"])
def test_the_default_table_reaches_meta_for_a_story_and_never_for_the_report(env, backend):
    env.setenv("META_MODEL_API_KEY", "test-key")
    env.setattr(config, "NARRATIVE_ROUTING", config_mod.load_config().NARRATIVE_ROUTING)
    env.setattr(config, "NARRATIVE_BACKEND", backend)
    story = "A low came around 3:12; they caught it and treated it."
    fakes = Fakes(env, backboard=RuntimeError("down"), meta=story, anthropic="Lowest 64 mg/dL at 3:12.")
    assert narrative.generate("family_story", {"level": "story_only", "name": "Mom"}, REPORT) == story
    assert fakes.names() == ["meta"]
    fakes = Fakes(env, backboard=RuntimeError("down"), meta="x", anthropic="Lowest 64 mg/dL at 3:12.")
    assert narrative.generate("morning_report", {}, REPORT) == "Lowest 64 mg/dL at 3:12."
    assert "meta" not in fakes.names() and fakes.names()[-1] == "anthropic"


def test_template_backend_calls_nothing_even_with_a_meta_key(env):
    env.setenv("META_MODEL_API_KEY", "test-key")
    env.setattr(config, "NARRATIVE_ROUTING", config_mod.load_config().NARRATIVE_ROUTING)
    fakes = Fakes(env, meta="x", anthropic="x", backboard="x")
    narrative.generate("family_story", {"level": "story_only", "name": "Mom"}, REPORT)
    assert fakes.calls == []
