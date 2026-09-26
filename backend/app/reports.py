"""Morning report (chris.md step 11): overnight stats, a matplotlib PNG, a
plain-language narrative (the direct Claude call until R13, then
narrative.py's chain with the validator), email + stored report + the morning
screen; a pre-generated fallback for the no-network demo. Family Story rides
this job (F3)."""

from __future__ import annotations

from datetime import date

from .contracts import MorningReport


def build_report(night_date: date) -> MorningReport:
    raise NotImplementedError("step 11: morning report")
