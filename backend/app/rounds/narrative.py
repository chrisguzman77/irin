"""R13 / R13+: ONE narrative module for every Irin narrative (morning report,
card narratives, buddy lines, Family Story). Chain of four links with a 20 s
timeout: Backboard -> Meta direct (Meta Model API, provider "meta" chosen per
task by NARRATIVE_ROUTING) -> direct Anthropic -> the deterministic template.
The no-invented-numbers validator runs after every link (every numeric token
must exist in the computed metrics); a card narrative also passes the
second-opinion check from a different provider; disagreement ships the
template. Muse prompts never carry a glucose value (invariant 22).

Tasks and the context each reads (metrics are always the computed numbers):
  card            kind, headline, confidence, scope (doctor)
  morning_report  metrics = reports' overnight stats; scope (patient)
  family_story    level (story_only | story_and_view), name, scope (family, memory Readonly)
  buddy_line      kind (all_quiet | close_out), name, treated, recovered; metrics may hold call_at "HH:MM"
  buddy_intro     name (first name only), shared_languages; metrics may hold hours_covered
`scope` is a contracts.NarrativeScope (or its dict); the Backboard thread it
names is kept in the kv table. An error or timeout in a link falls to the next
link; an invented number, a no-data night told as fine, or a second opinion that
is not a clean "no" ships the template. Backboard's client is async and runs
under asyncio.run, so call generate from a worker thread, never the event loop
(inside a running loop that link fails and the chain moves on)."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, time
from pathlib import Path

from ..config import REPO_ROOT, config
from ..contracts import NarrativeScope, SecondOpinion

log = logging.getLogger(__name__)

TIMEOUT_S = 20.0
META_BASE_URL = "https://api.meta.ai/v1"
META_MODEL = "muse-spark-1.3"  # Meta direct's model when it stands in for an openrouter row
THRESHOLDS = (54, 70, 180)  # the consensus range limits any narrative may name
SECOND_OPINION_PROMPT = REPO_ROOT / "ml" / "prompts" / "second_opinion.md"

_ROUTING_KEY = {"card": "clinician_card", "buddy_intro": "match_explanation"}
_SCOPE_KIND = {"card": "doctor", "morning_report": "patient", "family_story": "family",
               "buddy_line": "buddy", "buddy_intro": "buddy"}
_GLUCOSE_KEY = re.compile(r"mgdl|mg_dl|glucose|sgv|nadir|mmol|(^|_)bg($|_)", re.IGNORECASE)

CARD_SYSTEM = (
    "You write the narrative on a clinical signal card a patient's own doctor reads. Two to three sentences, "
    "plain language, describing only what the measurements show. Never recommend, suggest, or imply any action; "
    "never mention a dose or a number of units. Use ONLY the numbers given, exactly as given; never invent, "
    "round differently, or derive a new number."
)
BUDDY_LINE_SYSTEM = (
    "You write one or two short, calm sentences between two adults with type 1 diabetes who back each other up "
    "overnight: either the morning 'all quiet' line or the close-out after a night event. First names only, no "
    "glucose values, no medical advice. Use ONLY the numbers and clock times given, exactly as given."
)
BUDDY_INTRO_SYSTEM = (
    "You introduce two adults with type 1 diabetes who could back each other up overnight, in one or two warm "
    "sentences explaining why they match. First names only, no glucose values, no medical advice. Use ONLY the "
    "numbers given, exactly as given."
)


# --- the validator ---

_TOKEN = re.compile(r"\d{1,2}:\d{2}|\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
_CLOCK = re.compile(r"(\d{1,2}):(\d{2})")


def numeric_tokens(text: str) -> set[str]:
    """Integers, decimals (thousands commas dropped), percentages (the digits
    before %), and clock times; "6 of 8" is the two tokens 6 and 8."""
    return {m.group(0).replace(",", "") for m in _TOKEN.finditer(text)}


def _clock_forms(h: int, m: int) -> set[str]:
    return {f"{h:02d}:{m:02d}", f"{h}:{m:02d}", f"{h % 12 or 12}:{m:02d}"}


def _number_forms(v: float) -> set[str]:
    a = abs(v)  # "down 22" matches a metric of -22; the sign is never a token
    forms = {f"{a:g}", f"{round(a)}", f"{a:.1f}"}
    if float(a).is_integer():
        forms.add(str(int(a)))
    return forms


def _allowed(value, forms: set[str]) -> None:
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, dict):
        for v in value.values():
            _allowed(v, forms)
    elif isinstance(value, (list, tuple, set)):
        for v in value:
            _allowed(v, forms)
    elif isinstance(value, (int, float)):
        forms |= _number_forms(value)
    elif isinstance(value, (datetime, time)):
        forms |= _clock_forms(value.hour, value.minute)
    elif isinstance(value, str):
        s = value.strip()
        if (m := _CLOCK.fullmatch(s)):
            forms |= _clock_forms(int(m.group(1)), int(m.group(2)))
        elif re.fullmatch(r"-?\d+(?:\.\d+)?", s):
            forms |= _number_forms(float(s))


def validate(text: str, metrics: dict) -> bool:
    """True when every numeric token in the text exists in the metrics (any
    depth; rounding to a whole number or one decimal allowed; clock times in
    24- or 12-hour form) or is one of the range THRESHOLDS."""
    forms: set[str] = set()
    _allowed(metrics, forms)
    _allowed(list(THRESHOLDS), forms)
    return all(tok in forms for tok in numeric_tokens(text))


# --- templates and prompts ---


def _clock_12h(value: str) -> str:
    m = _CLOCK.fullmatch(value.strip())
    return f"{int(m.group(1)) % 12 or 12}:{m.group(2)}" if m else value


def _buddy_line_template(context: dict, metrics: dict) -> str:
    name = context.get("name") or "Your buddy"
    if context.get("kind") != "close_out":
        return "All quiet last night: nothing reached the buddy rung."
    text = f"{name}'s okay." if context.get("recovered") else "The night event is closed."
    if metrics.get("call_at"):
        text += f" Your call at {_clock_12h(str(metrics['call_at']))} got through."
    if context.get("treated"):
        text += f" {name} treated and recovered." if context.get("recovered") else f" {name} treated."
    return text


def _buddy_intro_template(context: dict, metrics: dict) -> str:
    text = f"Meet {context.get('name') or 'your match'}, an adult with type 1 diabetes who can back you up overnight."
    if metrics.get("hours_covered"):
        text += f" Your nights overlap by {metrics['hours_covered']:g} hours."
    if context.get("shared_languages"):
        text += f" You both speak {', '.join(context['shared_languages'])}."
    return text


def _template(task: str, context: dict, metrics: dict) -> str:
    if task == "card":
        from .cards import template_narrative
        return template_narrative(context.get("kind", ""), context.get("headline", ""), metrics,
                                  context.get("confidence") or {})
    if task == "morning_report":
        from ..reports import template_narrative
        return template_narrative(metrics)
    if task == "family_story":
        from ..family_story import template_story
        return template_story(metrics, context.get("level", "story_only"), context.get("name", ""))
    if task == "buddy_line":
        return _buddy_line_template(context, metrics)
    if task == "buddy_intro":
        return _buddy_intro_template(context, metrics)
    raise ValueError(f"unknown narrative task {task!r}")


def _scrub(value):
    """A Muse prompt's metrics: every glucose-valued key removed, at any depth."""
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items() if not _GLUCOSE_KEY.search(str(k))}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def _prompt_context(context: dict, muse: bool) -> dict:
    """Words only: the scope stays out; a Muse prompt drops any string with a digit in it."""
    out = {k: v for k, v in context.items() if k not in ("scope", "confidence")}
    if muse:
        out = {k: v for k, v in out.items() if not (isinstance(v, str) and re.search(r"\d", v))
               and isinstance(v, (str, bool, list))}
    return out


def _system_and_prompt(task: str, context: dict, metrics: dict, muse: bool) -> tuple[str, str]:
    data = json.dumps(metrics, default=str)
    if task == "morning_report":
        from ..reports import SYSTEM_PROMPT, narrative_prompt
        return SYSTEM_PROMPT, narrative_prompt(metrics)
    if task == "family_story":
        from ..family_story import FAMILY_SYSTEM_PROMPT, family_prompt
        return FAMILY_SYSTEM_PROMPT, family_prompt(metrics, context.get("level", "story_only"))
    system = {"card": CARD_SYSTEM, "buddy_line": BUDDY_LINE_SYSTEM, "buddy_intro": BUDDY_INTRO_SYSTEM}[task]
    about = json.dumps(_prompt_context(context, muse), default=str)
    return system, f"About: {about}\nThe only numbers you may use: {data}"


def _passes(task: str, context: dict, text: str, metrics: dict) -> bool:
    if not validate(text, metrics):
        return False
    if task == "family_story":
        from ..family_story import validate_family_text
        return validate_family_text(text, context.get("level", "story_only"), metrics)
    if task == "morning_report" and not metrics.get("readings") and "fine" in text.lower():
        return False  # a no-data night is never told as fine
    return True


# --- the links (tests replace these three with fakes; nothing else reaches the network) ---


def _scope_key(scope: NarrativeScope) -> str:
    return f"narrative_scope:{scope.kind}:{scope.scope_id}"


def _call_backboard(system: str, prompt: str, *, provider: str, model: str,
                    scope: NarrativeScope | None, json_output: bool = False) -> str:
    """One assistant + thread per NarrativeScope, created on first use by
    send_message and kept in kv; family scopes read memory, never write it.
    scope None = a fresh stateless thread with memory off (the second opinion)."""
    from backboard import BackboardClient

    from .. import store

    if not config.BACKBOARD_API_KEY:
        raise RuntimeError("BACKBOARD_API_KEY not set")
    stored = None
    if scope is not None:
        raw = store.get_kv(_scope_key(scope))
        stored = NarrativeScope.model_validate_json(raw) if raw else scope
    memory = "off" if scope is None else ("Readonly" if scope.kind == "family" else "Auto")

    async def send():
        async with BackboardClient(api_key=config.BACKBOARD_API_KEY, timeout=int(TIMEOUT_S)) as client:
            return await client.send_message(
                content=prompt, system_prompt=system, llm_provider=provider, model_name=model,
                thread_id=stored.thread_id if stored else None,
                assistant_id=stored.backboard_assistant_id if stored else None,
                memory=memory, stream=False, json_output=json_output or None)

    response = asyncio.run(asyncio.wait_for(send(), TIMEOUT_S))
    text = (response.content or "").strip()
    if not text:
        raise RuntimeError("empty narrative")
    if stored is not None and not stored.thread_id:
        store.set_kv(_scope_key(scope), stored.model_copy(update={
            "thread_id": str(response.thread_id),
            "backboard_assistant_id": str(getattr(response, "assistant_id", "") or "") or None,
        }).model_dump_json())
    return text


def _call_meta(system: str, prompt: str, *, model: str, json_output: bool = False) -> str:
    """The Meta Model API through the OpenAI SDK (base_url)."""
    from openai import OpenAI

    if not config.META_MODEL_API_KEY:
        raise RuntimeError("META_MODEL_API_KEY not set")
    client = OpenAI(api_key=config.META_MODEL_API_KEY, base_url=META_BASE_URL, timeout=TIMEOUT_S, max_retries=0)
    extra = {"response_format": {"type": "json_object"}} if json_output else {}
    response = client.chat.completions.create(
        model=model, messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        temperature=0, **extra)
    text = (response.choices[0].message.content or "").strip() if response.choices else ""
    if not text:
        raise RuntimeError("empty narrative")
    return text


def _call_anthropic(system: str, prompt: str, *, model: str) -> str:
    import anthropic

    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, timeout=TIMEOUT_S, max_retries=0)
    response = client.messages.create(model=model, max_tokens=600, system=system,
                                      messages=[{"role": "user", "content": prompt}])
    if response.stop_reason == "refusal":
        raise RuntimeError("refused")
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if not text:
        raise RuntimeError("empty narrative")
    return text


# --- the chain ---


def _route(task: str) -> tuple[str, str]:
    row = config.NARRATIVE_ROUTING.get(_ROUTING_KEY.get(task, task)) or []
    if len(row) == 2:
        return str(row[0]), str(row[1])
    from ..reports import DEFAULT_MODEL
    return "anthropic", DEFAULT_MODEL


def _scope(task: str, context: dict) -> NarrativeScope:
    s = context.get("scope")
    if isinstance(s, NarrativeScope):
        return s
    if isinstance(s, dict):
        return NarrativeScope.model_validate(s)
    return NarrativeScope(kind=_SCOPE_KIND[task], scope_id="default")


def _links(task: str, context: dict) -> list[tuple[str, str, bool, object]]:
    """(link name, writer provider, is Muse, call(system, prompt) -> text), in order."""
    backend = config.NARRATIVE_BACKEND
    if backend not in ("backboard", "anthropic"):
        return []
    provider, model = _route(task)
    links: list[tuple[str, str, bool, object]] = []
    if backend == "backboard" and provider != "meta":  # Backboard carries no Meta provider
        scope = _scope(task, context)
        links.append(("backboard", provider, "muse" in model.lower(),
                      lambda s, p: _call_backboard(s, p, provider=provider, model=model, scope=scope)))
    if provider == "meta" or (provider == "openrouter" and backend == "backboard"):
        meta_model = model if provider == "meta" else META_MODEL
        links.append(("meta", "meta", True, lambda s, p: _call_meta(s, p, model=meta_model)))
    from ..reports import DEFAULT_MODEL
    anthropic_model = model if provider == "anthropic" else DEFAULT_MODEL
    links.append(("anthropic", "anthropic", False, lambda s, p: _call_anthropic(s, p, model=anthropic_model)))
    return links


def _second_opinion_prompt() -> tuple[str, str]:
    """(system, user template) from ml/prompts/second_opinion.md: its first two fenced blocks."""
    blocks = re.findall(r"```\n(.*?)```", SECOND_OPINION_PROMPT.read_text(encoding="utf-8"), re.DOTALL)
    return blocks[0].strip(), blocks[1].strip()


def _second_opinion_ok(text: str, context: dict, metrics: dict, writer: str) -> bool:
    """Fails closed: no row, the writer's own provider, a Muse checker (the card
    text carries glucose values), any error, or anything but two falses."""
    row = config.NARRATIVE_ROUTING.get("second_opinion") or []
    if len(row) != 2:
        log.warning("no second_opinion routing row; the card ships the template")
        return False
    provider, model = str(row[0]), str(row[1])
    if provider == writer or provider == "meta" or "muse" in model.lower():
        log.warning("second opinion provider %s is not usable for a card written by %s", provider, writer)
        return False
    try:
        system, user = _second_opinion_prompt()
        prompt = (user.replace("{card_kind}", str(context.get("kind", "")))
                      .replace("{metrics_json}", json.dumps(metrics, default=str))
                      .replace("{narrative_text}", text))
        if provider == "anthropic":
            raw = _call_anthropic(system, prompt, model=model)
        else:
            raw = _call_backboard(system, prompt, provider=provider, model=model, scope=None, json_output=True)
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        verdict = SecondOpinion.model_validate_json(match.group(0) if match else raw)
    except Exception as e:
        log.warning("second opinion failed (%s); the card ships the template", type(e).__name__)
        return False
    return not verdict.invented_number and not verdict.advice


def generate(task: str, context: dict, metrics: dict) -> str:
    """The narrative for one task: the first link that answers, if it passes
    the validator (and, for a card, the second opinion); else the template."""
    fallback = _template(task, context, metrics)
    for name, writer, muse, call in _links(task, context):
        sent = _scrub(metrics) if muse else metrics
        system, prompt = _system_and_prompt(task, context, sent, muse)
        try:
            text = call(system, prompt).strip()
        except Exception as e:  # no key, no network, refusal, timeout: the next link
            log.warning("narrative link %s failed for %s (%s)", name, task, type(e).__name__)
            continue
        if not _passes(task, context, text, sent):
            log.warning("narrative from %s failed the validator for %s; using the template", name, task)
            return fallback
        if task == "card" and not _second_opinion_ok(text, context, metrics, writer):
            return fallback
        return text
    return fallback
