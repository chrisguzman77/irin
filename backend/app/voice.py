"""Voice logging (chris.md step 7). parse() turns "log 45 carbs and 5 units"
into numbers with a regex and nothing else: the text never reaches a shell,
eval, or an SQL string. Any insulin value is ECHOED and needs an explicit
confirm() before a Treatment exists (invariant 2); a partial parse names the
missing piece; a pending entry older than TIMEOUT_S on clock.py is discarded.
Carbs-only entries are stored at once (no insulin, nothing to confirm)."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from datetime import timedelta

from . import store
from .clock import clock
from .contracts import Treatment

TIMEOUT_S = 10
MAX_CARBS_G = 300
MAX_UNITS = 50

_NUM = r"(\d+(?:\.\d+)?)"
_CARBS = re.compile(_NUM + r"\s*(?:g\b|grams?\b|carbs?\b|carbohydrates?\b)|(?:carbs?|carbohydrates?)\s*(?:of\s*)?" + _NUM, re.I)
_UNITS = re.compile(_NUM + r"\s*(?:u\b|units?\b|iu\b)|(?:units?|insulin|bolus)\s*(?:of\s*)?" + _NUM, re.I)
_BARE_CARB_WORD = re.compile(r"\b(?:carbs?|carbohydrates?)\b", re.I)
_BARE_UNIT_WORD = re.compile(r"\b(?:units?|insulin|bolus)\b", re.I)
_BARE_NUMBER = re.compile(_NUM)


@dataclass(frozen=True)
class ParseResult:
    carbs_g: float | None = None
    insulin_units: float | None = None
    missing: tuple[str, ...] = ()  # "carbs amount" | "insulin amount" | "unit"
    error: str | None = None

    @property
    def complete(self) -> bool:
        return not self.missing and self.error is None and (self.carbs_g is not None or self.insulin_units is not None)

    def echo(self) -> str:
        parts = []
        if self.carbs_g is not None:
            parts.append(f"{self.carbs_g:g} carbs")
        if self.insulin_units is not None:
            parts.append(f"{self.insulin_units:g} units")
        return " and ".join(parts) + ", save?" if parts else ""


def _first(rx: re.Pattern, text: str) -> float | None:
    m = rx.search(text)
    if not m:
        return None
    return float(next(g for g in m.groups() if g is not None))


def parse(text: str) -> ParseResult:
    text = (text or "").strip()
    if not text or len(text) > 200:
        return ParseResult(error="nothing to log" if not text else "too long")
    carbs = _first(_CARBS, text)
    units = _first(_UNITS, text)
    missing: list[str] = []
    if carbs is None and _BARE_CARB_WORD.search(text):
        missing.append("carbs amount")
    if units is None and _BARE_UNIT_WORD.search(text):
        missing.append("insulin amount")
    if carbs is None and units is None and not missing:
        if _BARE_NUMBER.search(text):
            missing.append("unit")  # "log 45": carbs or units?
        else:
            return ParseResult(error="no amount found")
    if carbs is not None and not (0 <= carbs <= MAX_CARBS_G):
        return ParseResult(error=f"carbs out of range (0-{MAX_CARBS_G} g)")
    if units is not None and not (0 <= units <= MAX_UNITS):
        return ParseResult(error=f"units out of range (0-{MAX_UNITS})")
    return ParseResult(carbs_g=carbs, insulin_units=units, missing=tuple(missing))


@dataclass
class Pending:
    pending_id: str
    parsed: ParseResult
    created_at: object  # datetime from clock.now()


@dataclass
class VoiceLogger:
    _pending: dict[str, Pending] = field(default_factory=dict)

    def _expire(self) -> None:
        now = clock.now()
        for pid in [p for p, e in self._pending.items() if now - e.created_at > timedelta(seconds=TIMEOUT_S)]:
            del self._pending[pid]

    def submit(self, text: str) -> dict:
        """Parse and act. Returns {status, echo|missing|error, pending_id?, stored?}."""
        self._expire()
        r = parse(text)
        if r.error:
            return {"status": "error", "error": r.error}
        if r.missing:
            return {"status": "incomplete", "missing": list(r.missing), "ask": f"how many {r.missing[0].split()[0]}?"}
        if r.insulin_units is None:  # carbs only: nothing to confirm
            t = Treatment(timestamp=clock.now(), kind="carbs", carbs_g=r.carbs_g, confirmed=True)
            store.insert_treatment(t)
            return {"status": "stored", "stored": [t.model_dump(mode="json")]}
        pid = secrets.token_hex(8)
        self._pending[pid] = Pending(pid, r, clock.now())
        return {"status": "needs_confirm", "pending_id": pid, "echo": r.echo(), "timeout_s": TIMEOUT_S}

    def confirm(self, pending_id: str) -> dict:
        self._expire()
        p = self._pending.pop(pending_id, None)
        if p is None:
            return {"status": "expired"}  # discarded: nothing stored
        now = clock.now()
        stored = []
        if p.parsed.carbs_g is not None:
            stored.append(Treatment(timestamp=now, kind="carbs", carbs_g=p.parsed.carbs_g, confirmed=True))
        stored.append(Treatment(timestamp=now, kind="bolus", insulin_units=p.parsed.insulin_units, confirmed=True))
        for t in stored:
            store.insert_treatment(t)
        return {"status": "stored", "stored": [t.model_dump(mode="json") for t in stored]}

    def cancel(self, pending_id: str) -> dict:
        self._pending.pop(pending_id, None)
        return {"status": "cancelled"}
