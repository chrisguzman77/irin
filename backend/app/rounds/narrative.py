"""R13 / R13+: ONE narrative module for every Irin narrative (morning report,
card narratives, buddy lines, Family Story). Chain of four links with a 20 s
timeout: Backboard -> Meta direct (Meta Model API, provider "meta" chosen per
task by NARRATIVE_ROUTING) -> direct Anthropic -> the deterministic template.
The no-invented-numbers validator runs after every link (every numeric token
must exist in the computed metrics); a card narrative also passes the
second-opinion check from a different provider; disagreement ships the
template. Muse prompts never carry a glucose value (invariant 22).

Tasks and the context each reads (metrics are always the computed numbers):
  card            kind, headline, confidence, scope (a doctor NarrativeScope)
  morning_report  metrics = reports' overnight stats; scope (default: the patient)
  family_story    level (story_only | story_and_view), name, scope (family, memory Readonly)
  buddy_line      kind (all_quiet | close_out), name, treated, recovered, scope; metrics may hold call_at "HH:MM"
  buddy_intro     name (first name only), shared_languages, scope; metrics may hold hours_covered
`scope` is a contracts.NarrativeScope (or its dict); Backboard keeps one
assistant and thread per scope (ids in the kv table). Only morning_report has a
default scope; any other task without one, and any Muse model, runs with memory
off. ONE 20 s deadline covers the whole chain and the second opinion. An error,
timeout, or empty reply falls to the next link; an invented number, a card
naming a dose, or a second opinion that is not a clean "no" ships the template;
a no-data morning is always the template. generate() never runs on an event
loop (it returns the template there): callers run it in a worker thread."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime
from datetime import time as dtime
from decimal import ROUND_HALF_UP, Decimal

from ..config import REPO_ROOT, config
from ..contracts import NarrativeScope, SecondOpinion

log = logging.getLogger(__name__)

TIMEOUT_S = 20.0
MIN_LINK_S = 1.0  # under this much of the deadline left, the template ships
META_BASE_URL = "https://api.meta.ai/v1"
META_MODEL = "muse-spark-1.3"  # Meta direct's model when it stands in for an openrouter row
THRESHOLDS = (54, 70, 180)  # the consensus range limits any narrative may name
SECOND_OPINION_PROMPT = REPO_ROOT / "ml" / "prompts" / "second_opinion.md"

_ROUTING_KEY = {"card": "clinician_card", "buddy_intro": "match_explanation"}

# What a Muse prompt may carry, per task (invariant 22): counts, shares, and times, never a glucose value
# or an insulin amount. Anything not listed is dropped, at every depth.
_STORY_KEYS = frozenset({"readings", "coverage_pct", "tir_pct", "tbr_pct", "tar_pct", "minutes_below_70",
                         "low_at", "high_at", "carbs_g"})
_BUDDY_KEYS = frozenset({"call_at", "treated_at", "recovered_at", "hours_covered", "nights", "alerts", "calls"})
_MUSE_KEYS = {"morning_report": _STORY_KEYS, "family_story": _STORY_KEYS,
              "buddy_line": _BUDDY_KEYS, "buddy_intro": _BUDDY_KEYS, "card": frozenset()}

_now = time.monotonic  # the chain's deadline is network wall time, not replay time (tests replace it)

CARD_SYSTEM = (
    "You write the narrative on a clinical signal card a patient's own doctor reads. Two to three sentences, "
    "plain language, describing only what the measurements show. Never recommend, suggest, or imply any action; "
    "never mention a dose or a number of units. Use ONLY the numbers given, exactly as given, in digits; never "
    "invent, round differently, spell out, or derive a new number."
)
BUDDY_LINE_SYSTEM = (
    "You write one or two short, calm sentences between two adults with type 1 diabetes who back each other up "
    "overnight: either the morning 'all quiet' line or the close-out after a night event. First names only, no "
    "glucose values, no medical advice. Use ONLY the numbers and clock times given, exactly as given, in digits."
)
BUDDY_INTRO_SYSTEM = (
    "You introduce two adults with type 1 diabetes who could back each other up overnight, in one or two warm "
    "sentences explaining why they match. First names only, no glucose values, no medical advice. Use ONLY the "
    "numbers given, exactly as given, in digits."
)


# --- the validator ---

# A numeric token: an optional sign glued to the digits, never digits glued to a preceding letter or to a
# letter-hyphen ("T1D", "GLP-1"); clock times with optional seconds; thousands commas.
_TOKEN = re.compile(
    r"(?<![A-Za-z\d])(?<![A-Za-z]-)(?P<sign>[-+−](?=\d))?(?<![A-Za-z]-)"
    r"(?P<num>\d{1,2}:\d{2}(?::\d{2})?|\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
)
_CLOCK = re.compile(r"(\d{1,2}):(\d{2})(?::(\d{2}))?")
_DOMAIN_TERMS = re.compile(r"\btype\s+1\b|\bT1D\b|\bGLP-1\b|\blevel\s+[12]\b", re.IGNORECASE)
_STEP_TERM = re.compile(r"\bstep\s+(\d+)\b", re.IGNORECASE)
_RISE_WORDS = re.compile(r"\b(rose|rise|rises|rising|risen|up|higher|increased?|climbed|gained?)\b\W*(\w+\W+){0,2}$",
                         re.IGNORECASE)
_DOSE_WORDS = re.compile(r"\bunits?\b|\bdoses?\b|\bmg\b(?!\s*/\s*dl)", re.IGNORECASE)
_DOSE_KEY = re.compile(r"unit|dose|insulin", re.IGNORECASE)


def _clock_forms(h: int, m: int, s: int | None = None) -> set[str]:
    forms = {f"{h:02d}:{m:02d}", f"{h}:{m:02d}", f"{h % 12 or 12}:{m:02d}"}
    if s is not None:
        forms |= {f"{f}:{s:02d}" for f in forms}
    return forms


def _number_forms(v) -> set[str]:
    a = abs(Decimal(str(v)))  # the sign is checked separately
    forms = {str(a.quantize(Decimal(1), ROUND_HALF_UP)), str(a.quantize(Decimal("0.1"), ROUND_HALF_UP)),
             f"{float(a):g}"}
    if a == a.to_integral_value():
        forms.add(str(int(a)))
    return forms


def _collect(value, forms: dict[str, set[str]], key: str = "", only_key: re.Pattern | None = None) -> None:
    """forms: token form -> the signs ("+", "-") a metric carries it with; clock forms carry none.
    only_key: collect only values under a key matching it (the card dose guard)."""
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, dict):
        for k, v in value.items():
            _collect(v, forms, str(k), only_key)
        return
    if isinstance(value, (list, tuple, set)):
        for v in value:
            _collect(v, forms, key, only_key)
        return
    if only_key is not None and not only_key.search(key):
        return
    if isinstance(value, (int, float)):
        for f in _number_forms(value):
            forms.setdefault(f, set()).add("-" if value < 0 else "+")
    elif isinstance(value, (datetime, dtime)):
        for f in _clock_forms(value.hour, value.minute, value.second or None):
            forms.setdefault(f, set())
    elif isinstance(value, str):
        s = value.strip()
        if (m := _CLOCK.fullmatch(s)):
            for f in _clock_forms(int(m.group(1)), int(m.group(2)), int(m.group(3)) if m.group(3) else None):
                forms.setdefault(f, set())
        elif re.fullmatch(r"[-+]?\d+(?:\.\d+)?", s):
            _collect(float(s), forms, key, only_key)


def _forms(metrics: dict) -> dict[str, set[str]]:
    forms: dict[str, set[str]] = {}
    _collect(metrics, forms)
    _collect(list(THRESHOLDS), forms)
    return forms


def _strip_terms(text: str, metrics: dict) -> str:
    """Domain terms that carry a digit but no value: type 1, T1D, GLP-1, Level 1/2, and Step N when N is a
    step the metrics name."""
    steps: dict[str, set[str]] = {}
    _collect(metrics, steps, only_key=re.compile("step", re.IGNORECASE))
    text = _DOMAIN_TERMS.sub(" ", text)
    return _STEP_TERM.sub(lambda m: " " if m.group(1) in steps else m.group(0), text)


def _tokens(text: str) -> list[tuple[str, str, int]]:
    """(sign, number, start) for every numeric token; "3am" and "42mg" are the tokens 3 and 42."""
    return [((m.group("sign") or "").replace("−", "-"), m.group("num"), m.start())
            for m in _TOKEN.finditer(text)]


def numeric_tokens(text: str) -> set[str]:
    return {num for _, num, _ in _tokens(text)}


def _token_ok(sign: str, num: str, before: str, forms: dict[str, set[str]]) -> bool:
    if "," in num and ":" not in num:  # "1,440" as one number, or "70,180" as two
        joined = num.replace(",", "")
        return _token_ok(sign, joined, before, forms) or all(
            _token_ok("", part, before, forms) for part in num.split(","))
    signs = forms.get(num)
    if signs is None:
        return False
    if ":" in num:
        return not sign
    if sign:
        return sign in signs
    if _RISE_WORDS.search(before) and signs == {"-"}:  # "rose 22" when the metric is -22
        return False
    return True


def validate(text: str, metrics: dict) -> bool:
    """True when every numeric token in the text exists in the metrics (any
    depth; whole-number or one-decimal rounding, half up; clock times in 24- or
    12-hour form; a written sign must agree with the metric's) or is one of the
    range THRESHOLDS. Domain terms (type 1, GLP-1, Level 2, Step N) are not numbers."""
    forms = _forms(metrics)
    stripped = _strip_terms(text, metrics)
    return all(_token_ok(sign, num, stripped[max(0, at - 40):at], forms) for sign, num, at in _tokens(stripped))


def _names_a_dose(text: str, metrics: dict) -> bool:
    """Invariant 7: a card never names a dose, by word or by any number stored under a unit/dose/insulin key."""
    if _DOSE_WORDS.search(text):
        return True
    dose_forms: dict[str, set[str]] = {}
    _collect(metrics, dose_forms, only_key=_DOSE_KEY)
    return any(num in dose_forms for _, num, _ in _tokens(_strip_terms(text, metrics)))


# --- templates and prompts ---


def _clock_12h(value: str) -> str:
    m = _CLOCK.fullmatch(value.strip())
    return f"{int(m.group(1)) % 12 or 12}:{m.group(2)}" if m else value


def _shown(v) -> str:
    return f"{v:g}" if isinstance(v, float) else str(v)


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
        text += f" Your nights overlap by {_shown(metrics['hours_covered'])} hours."
    if context.get("shared_languages"):
        text += f" You both speak {', '.join(str(x) for x in context['shared_languages'])}."
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


def _keep(value, allowed: frozenset):
    if isinstance(value, dict):
        return {k: _keep(v, allowed) for k, v in value.items() if k in allowed}
    if isinstance(value, list):
        return [_keep(v, allowed) for v in value if isinstance(v, (dict, list))]
    return value


def _muse_metrics(task: str, metrics: dict) -> dict:
    """A Muse prompt's metrics: the task's allowlist only, at every depth (list elements filtered the same way)."""
    return _keep(metrics, _MUSE_KEYS.get(task, frozenset()))


def _prompt_context(context: dict, muse: bool) -> dict:
    """Words only: the scope and confidence stay out; a Muse prompt keeps only strings and bools without a digit."""
    out = {k: v for k, v in context.items() if k not in ("scope", "confidence")}
    if muse:
        def clean(v):
            return isinstance(v, bool) or (isinstance(v, str) and not re.search(r"\d", v))
        out = {k: ([x for x in v if clean(x)] if isinstance(v, list) else v)
               for k, v in out.items() if isinstance(v, list) or clean(v)}
    return out


def _system_and_prompt(task: str, context: dict, metrics: dict, muse: bool) -> tuple[str, str]:
    if task == "morning_report":
        from ..reports import SYSTEM_PROMPT, narrative_prompt
        return SYSTEM_PROMPT, narrative_prompt(metrics)
    if task == "family_story":
        from ..family_story import FAMILY_SYSTEM_PROMPT, family_prompt
        return FAMILY_SYSTEM_PROMPT, family_prompt(metrics, context.get("level", "story_only"))
    system = {"card": CARD_SYSTEM, "buddy_line": BUDDY_LINE_SYSTEM, "buddy_intro": BUDDY_INTRO_SYSTEM}[task]
    about = json.dumps(_prompt_context(context, muse), default=str)
    return system, f"About: {about}\nThe only numbers you may use: {json.dumps(metrics, default=str)}"


def _passes(task: str, context: dict, text: str, metrics: dict) -> bool:
    if not validate(text, metrics):
        return False
    if task == "card":
        return not _names_a_dose(text, metrics)
    from ..family_story import _NUMBER_WORDS, validate_family_text
    if _NUMBER_WORDS.search(text):  # a spelled-out number is a number the regex cannot check
        return False
    if task == "family_story":
        return validate_family_text(text, context.get("level", "story_only"), metrics)
    return True


# --- the links (tests replace these three with fakes; nothing else reaches the network) ---


def _scope_key(scope: NarrativeScope) -> str:
    return f"narrative_scope:{scope.kind}:{scope.scope_id}"


def _call_backboard(system: str, prompt: str, *, provider: str, model: str, scope: NarrativeScope | None,
                    timeout: float, json_output: bool = False) -> str:
    """One assistant per NarrativeScope (create_assistant on first use) and its
    thread, both ids kept in kv; family scopes read memory, never write it.
    scope None = a fresh thread on no assistant, memory off."""
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
        nonlocal stored
        async with BackboardClient(api_key=config.BACKBOARD_API_KEY, timeout=max(1, int(timeout))) as client:
            if stored is not None and not stored.backboard_assistant_id:
                assistant = await client.create_assistant(name=f"irin-{stored.kind}")
                stored = stored.model_copy(update={"backboard_assistant_id": str(assistant.assistant_id)})
                store.set_kv(_scope_key(scope), stored.model_dump_json())
            return await client.send_message(
                content=prompt, system_prompt=system, llm_provider=provider, model_name=model,
                thread_id=stored.thread_id if stored else None,
                assistant_id=stored.backboard_assistant_id if stored else None,
                memory=memory, stream=False, json_output=json_output or None)

    response = asyncio.run(asyncio.wait_for(send(), timeout))
    text = (response.content or "").strip()
    if stored is not None and not stored.thread_id and response.thread_id:
        store.set_kv(_scope_key(scope), stored.model_copy(update={"thread_id": str(response.thread_id)}).model_dump_json())
    return text


def _call_meta(system: str, prompt: str, *, model: str, timeout: float, json_output: bool = False) -> str:
    """The Meta Model API through the OpenAI SDK (base_url)."""
    from openai import OpenAI

    if not config.META_MODEL_API_KEY:
        raise RuntimeError("META_MODEL_API_KEY not set")
    client = OpenAI(api_key=config.META_MODEL_API_KEY, base_url=META_BASE_URL, timeout=timeout, max_retries=0)
    extra = {"response_format": {"type": "json_object"}} if json_output else {}
    response = client.chat.completions.create(
        model=model, messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        temperature=0, **extra)
    return (response.choices[0].message.content or "").strip() if response.choices else ""


def _call_anthropic(system: str, prompt: str, *, model: str, timeout: float) -> str:
    import anthropic

    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, timeout=timeout, max_retries=0)
    response = client.messages.create(model=model, max_tokens=600, system=system,
                                      messages=[{"role": "user", "content": prompt}])
    if response.stop_reason == "refusal":
        raise RuntimeError("refused")
    return "".join(block.text for block in response.content if block.type == "text").strip()


# --- the chain ---


def _is_muse(provider: str, model: str) -> bool:
    m = model.lower()
    return provider == "meta" or "muse" in m or m.startswith("meta")


def _family(provider: str, model: str) -> str:
    """The model family behind a provider/model pair: an openrouter row naming a Claude is still anthropic."""
    m = model.lower()
    if _is_muse(provider, model):
        return "meta"
    if "claude" in m or m.startswith("anthropic"):
        return "anthropic"
    if m.startswith(("gpt", "openai", "o1", "o3", "o4")):
        return "openai"
    return provider


def _route(task: str) -> tuple[str, str]:
    row = config.NARRATIVE_ROUTING.get(_ROUTING_KEY.get(task, task)) or []
    if len(row) == 2:
        return str(row[0]), str(row[1])
    from ..reports import DEFAULT_MODEL
    return "anthropic", DEFAULT_MODEL


def _scope(task: str, context: dict) -> NarrativeScope | None:
    """Only morning_report has a default (the device's one patient); without an explicit scope, memory is off."""
    s = context.get("scope")
    if isinstance(s, NarrativeScope):
        return s
    if isinstance(s, dict):
        return NarrativeScope.model_validate(s)
    return NarrativeScope(kind="patient", scope_id="default") if task == "morning_report" else None


def _links(task: str, context: dict) -> list[tuple[str, str, str, object]]:
    """(link name, writer provider, writer model, call(system, prompt, timeout) -> text), in order."""
    backend = config.NARRATIVE_BACKEND
    if backend not in ("backboard", "anthropic"):
        return []
    provider, model = _route(task)
    links: list[tuple[str, str, str, object]] = []
    if backend == "backboard" and provider != "meta":  # Backboard carries no Meta provider
        scope = None if _is_muse(provider, model) else _scope(task, context)
        links.append(("backboard", provider, model, lambda s, p, t: _call_backboard(
            s, p, provider=provider, model=model, scope=scope, timeout=t)))
    if provider == "meta" or (provider == "openrouter" and backend == "backboard"):
        meta_model = model if provider == "meta" else META_MODEL
        links.append(("meta", "meta", meta_model, lambda s, p, t: _call_meta(s, p, model=meta_model, timeout=t)))
    from ..reports import DEFAULT_MODEL
    anthropic_model = model if provider == "anthropic" else DEFAULT_MODEL
    links.append(("anthropic", "anthropic", anthropic_model,
                  lambda s, p, t: _call_anthropic(s, p, model=anthropic_model, timeout=t)))
    return links


def _second_opinion_prompt() -> tuple[str, str]:
    """(system, user template) from ml/prompts/second_opinion.md: its first two fenced blocks."""
    blocks = re.findall(r"```\n(.*?)```", SECOND_OPINION_PROMPT.read_text(encoding="utf-8"), re.DOTALL)
    return blocks[0].strip(), blocks[1].strip()


def _second_opinion_ok(text: str, context: dict, metrics: dict, writer: tuple[str, str], deadline: float) -> bool:
    """Fails closed: no row, the writer's own model family, a Muse checker (the
    card text carries glucose values), no time left, any error, or anything but two falses."""
    row = config.NARRATIVE_ROUTING.get("second_opinion") or []
    if len(row) != 2:
        log.warning("no second_opinion routing row; the card ships the template")
        return False
    provider, model = str(row[0]), str(row[1])
    if _is_muse(provider, model) or _family(provider, model) == _family(*writer):
        log.warning("second opinion %s is not usable for a card written by %s", provider, writer[0])
        return False
    remaining = deadline - _now()
    if remaining < MIN_LINK_S:
        log.warning("no time left for the second opinion; the card ships the template")
        return False
    try:
        system, user = _second_opinion_prompt()
        prompt = (user.replace("{card_kind}", str(context.get("kind", "")))
                      .replace("{metrics_json}", json.dumps(metrics, default=str))
                      .replace("{narrative_text}", text))
        if provider == "anthropic":
            raw = _call_anthropic(system, prompt, model=model, timeout=remaining)
        else:
            raw = _call_backboard(system, prompt, provider=provider, model=model, scope=None,
                                  timeout=remaining, json_output=True)
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        verdict = SecondOpinion.model_validate_json(match.group(0) if match else raw)
    except Exception as e:
        log.warning("second opinion failed (%s); the card ships the template", type(e).__name__)
        return False
    return not verdict.invented_number and not verdict.advice


def _on_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def generate(task: str, context: dict, metrics: dict) -> str:
    """The narrative for one task: the first link that answers inside the
    deadline, if it passes the validator (and, for a card, the second opinion);
    else the template."""
    fallback = _template(task, context, metrics)
    if _on_event_loop():
        log.warning("narrative.generate called on the event loop for %s; the template ships", task)
        return fallback
    if task == "morning_report" and not metrics.get("readings"):
        return fallback  # a no-data night is only ever told by the template
    deadline = _now() + TIMEOUT_S
    for name, provider, model, call in _links(task, context):
        remaining = deadline - _now()
        if remaining < MIN_LINK_S:
            log.warning("narrative deadline reached for %s; using the template", task)
            return fallback
        muse = _is_muse(provider, model)
        sent = _muse_metrics(task, metrics) if muse else metrics
        system, prompt = _system_and_prompt(task, context, sent, muse)
        try:
            text = (call(system, prompt, remaining) or "").strip()
        except Exception as e:  # no key, no network, refusal, timeout: the next link
            log.warning("narrative link %s failed for %s (%s)", name, task, type(e).__name__)
            continue
        if not text:
            log.warning("narrative link %s returned nothing for %s", name, task)
            continue
        if not _passes(task, context, text, sent):
            log.warning("narrative from %s failed the validator for %s; using the template", name, task)
            return fallback
        if task == "card" and not _second_opinion_ok(text, context, metrics, (provider, model), deadline):
            return fallback
        return text
    return fallback
