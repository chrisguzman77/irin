"""R8: the noise budget, in ONE place (invariant 10). One Standing Card per
type per patient per 14 days; one step check and one step gate per step;
green never interrupts (it goes to the weekly digest); red bypasses
everything but is deduplicated per event and capped at one per type per 12
hours; a Step Watch suspends Basal Check and absorbs Hypo Response into its
red safety card; nothing from stale nights or insufficient windows; a
patient's cards are never split across two programs on the same day."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

STANDING_INTERVAL = timedelta(days=14)
RED_CAP = timedelta(hours=12)
STANDING_KINDS = ("basal_check", "hypo_response", "follow_up")
PER_STEP_KINDS = ("step_check", "step_gate")


@dataclass(frozen=True)
class Sent:
    """What the budget remembers about a card that went out."""
    kind: str
    program: str
    status: str
    sent_at: datetime
    event_key: str | None = None  # a red's event, or plan:step for a step card


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    reason: str  # sent | digest | interval | red_cap | red_duplicate | watch | insufficient | per_step | program_day


def allow(kind: str, program: str, status: str, now: datetime, history: list[Sent], *,
          active_watch: bool = False, event_key: str | None = None) -> Verdict:
    if status == "insufficient":
        return Verdict(False, "insufficient")
    if program == "standing" and active_watch:
        return Verdict(False, "watch")  # Basal Check suspended; Hypo Response absorbed into the watch's safety card
    if status == "red":
        for s in history:
            if s.kind == kind and event_key is not None and s.event_key == event_key:
                return Verdict(False, "red_duplicate")
            if s.kind == kind and s.status == "red" and now - s.sent_at < RED_CAP:
                return Verdict(False, "red_cap")
        return Verdict(True, "sent")
    if status == "green":
        return Verdict(False, "digest")
    # amber
    if any(s.program != program and s.sent_at.date() == now.date() for s in history):
        return Verdict(False, "program_day")
    if kind in STANDING_KINDS:
        if any(s.kind == kind and now - s.sent_at < STANDING_INTERVAL for s in history):
            return Verdict(False, "interval")
    if kind in PER_STEP_KINDS:
        if any(s.kind == kind and s.event_key == event_key for s in history):
            return Verdict(False, "per_step")
    return Verdict(True, "sent")


def history_from_store(docs: list[dict[str, Any]]) -> list[Sent]:
    """store.select_cards() rows -> what the budget needs (sent cards only)."""
    out = []
    for d in docs:
        if d.get("status") != "sent":
            continue
        c = d["card"]
        key = f"{c.get('plan_id')}:{c.get('step_index')}" if c.get("plan_id") is not None else c.get("event_key")
        out.append(Sent(kind=c["kind"], program=c["program"], status=c["status"],
                        sent_at=datetime.fromisoformat(d["stored_at"]), event_key=key))
    return out
