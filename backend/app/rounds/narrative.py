"""R13 / R13+: ONE narrative module for every Irin narrative (morning report,
card narratives, buddy lines, Family Story). Chain of four links with a 20 s
timeout: Backboard -> Meta direct (Meta Model API, provider "meta" chosen per
task by NARRATIVE_ROUTING) -> direct Anthropic -> the deterministic template.
The no-invented-numbers validator runs after every link (every numeric token
must exist in the computed metrics); a card narrative also passes the
second-opinion check from a different provider; disagreement ships the
template. Muse prompts never carry a glucose value (invariant 22)."""

from __future__ import annotations


def generate(task: str, context: dict, metrics: dict) -> str:
    raise NotImplementedError("R13: narrative chain + validator")


def validate(text: str, metrics: dict) -> bool:
    raise NotImplementedError("R13: no-invented-numbers validator")
