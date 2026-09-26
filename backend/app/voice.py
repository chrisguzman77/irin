"""Voice logging parser (chris.md step 7): parses "log 45 carbs and 5 units";
any insulin value is ECHOED and requires explicit confirm before a Treatment
is stored (invariant 2); a partial parse asks for the missing piece; a 10 s
timeout on clock.py discards the pending entry. Parsed text never reaches
shell/eval/SQL strings."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ParsedEntry:
    carbs_g: float | None = None
    insulin_units: float | None = None
    missing: list[str] | None = None


def parse(text: str) -> ParsedEntry:
    raise NotImplementedError("step 7: voice parser")
