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

_NUM = re.compile(r"(?<![\d.])(-?\d+(?:\.\d+)?)(?![\d.])")
_CARB_WORDS = {"g", "gram", "grams", "carb", "carbs", "carbohydrate", "carbohydrates"}
_UNIT_WORDS = {"u", "iu", "unit", "units", "insulin", "bolus"}
_LABEL_WORDS = _CARB_WORDS | _UNIT_WORDS
_WORD = re.compile(r"[A-Za-z]+")
_SPACE = re.compile(r"\s*")


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


def _label(word: str) -> str:
    return "carbs" if word in _CARB_WORDS else "units"


def _label_for(text: str, m: re.Match) -> str | None:
    """carbs | units | None for one number, in this order: an explicit lead
    "<carbs|units|insulin> of 45"; the word right after the number ("45 carbs",
    "5 u") unless that word is itself the lead of the NEXT number ("insulin 5
    carbs 45"); a bare lead right before it ("insulin 5")."""
    before = [w.lower() for w in _WORD.findall(text[:m.start()])]
    if len(before) >= 2 and before[-1] == "of" and before[-2] in _LABEL_WORDS:
        return _label(before[-2])
    pos = _SPACE.match(text, m.end()).end()
    after = _WORD.match(text, pos)
    if after and after.group(0).lower() in _LABEL_WORDS:
        rest = _SPACE.match(text, after.end()).end()
        if text[rest:rest + 3].lower() == "of ":
            rest = _SPACE.match(text, rest + 3).end()
        nxt = _NUM.match(text, rest)
        if nxt is None:
            return _label(after.group(0).lower())
        # "5 carbs 45": "carbs" leads the 45 (which has no label of its own), not the 5;
        # "5 units 45 carbs": the 45 has its own trailing label, so "units" stays with the 5.
        pos2 = _SPACE.match(text, nxt.end()).end()
        after2 = _WORD.match(text, pos2)
        if after2 and after2.group(0).lower() in _LABEL_WORDS:
            return _label(after.group(0).lower())
    if before and before[-1] in _LABEL_WORDS:
        return _label(before[-1])
    return None


def parse(text: str) -> ParseResult:
    text = (text or "").strip()
    if not text or len(text) > 200:
        return ParseResult(error="nothing to log" if not text else "too long")
    text = text.replace(",", " ")
    numbers = list(_NUM.finditer(text))
    if any(m.group(1).startswith("-") for m in numbers):
        return ParseResult(error="negative amount")
    found: dict[str, list[float]] = {"carbs": [], "units": []}
    unlabeled = 0
    for m in numbers:
        label = _label_for(text, m)
        if label is None:
            unlabeled += 1
        else:
            found[label].append(float(m.group(1)))
    for label, vals in found.items():
        if len(set(vals)) > 1:
            return ParseResult(error=f"more than one {'insulin' if label == 'units' else 'carbs'} amount; say it once")
    carbs = found["carbs"][0] if found["carbs"] else None
    units = found["units"][0] if found["units"] else None
    words = {w.lower() for w in _WORD.findall(text)}
    missing: list[str] = []
    if carbs is None and words & _CARB_WORDS:
        missing.append("carbs amount")
    if units is None and words & _UNIT_WORDS:
        missing.append("insulin amount")
    if unlabeled and not missing and (carbs is None or units is None):
        missing.append("unit")  # "log 45": carbs or units?
    if carbs is None and units is None and not missing:
        return ParseResult(error="no amount found")
    if carbs is not None and not (0 < carbs <= MAX_CARBS_G):
        return ParseResult(error=f"carbs out of range (1-{MAX_CARBS_G} g)")
    if units is not None and not (0 < units <= MAX_UNITS):
        return ParseResult(error=f"units out of range (0.5-{MAX_UNITS})")
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

    def reset(self) -> None:
        """The mode switch moves the clock; nothing pending survives it."""
        self._pending.clear()
