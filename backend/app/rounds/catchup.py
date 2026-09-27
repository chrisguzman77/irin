"""R12: the replay seek's idempotent catch-up. A 24-week watch cannot be played
(4,032 hours; 67 hours at 60x), so the replay datasource jumps its clock and
this module generates every card that should exist by then. What should
have run is derived from the scenario's nights, the plan's dates, and the
stored records and card ids, never from "jobs that fired", so it survives
reboots and repeated seeks: a night already built is not rebuilt, a question
already asked is not asked twice, and every card goes through the same
budget (one per key, red deduplicated per event), so seeking twice never
duplicates a card.

The scenario companion (demo/scenarios/<name>.json, George's schema in
demo/scenarios/README.md) is overlaid as it is read: reason codes on history
replace the ledger's (inferred, labeled so), the plan starts the watch, the
symptom checks, injections, alarm events and recall answers are seeded once,
and the confirmed dose change anchors the Follow-up. Each morning is replayed
with clock.py advanced to it, so the 12-hour red cap and every clock-driven
rule see the same time they would have seen live."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable

from .. import store
from ..clock import clock
from ..contracts import AlarmEvent, LowEventRecall, SymptomCheck, TitrationPlan, Treatment
from .ledger import Ledger
from .low_events import LowEventDetector
from .messages import therapy_change_key
from .nights_adapter import night_window, parse_hhmm
from .recall import MorningRecall

log = logging.getLogger("irin.rounds.catchup")

MORNING_OFFSET = timedelta(minutes=5)  # the evaluate job's 07:05
OVERLAY_ANSWER_DELAY = timedelta(hours=2)  # a companion's recall answer lands two hours after the question


@dataclass
class Companion:
    """A scenario's companion JSON, parsed to contract objects."""

    scenario: str
    kind: str = "core"
    synthetic: bool = False
    reason_codes: dict[date, tuple[list[str], str]] = field(default_factory=dict)
    plan: TitrationPlan | None = None
    dose_change: dict[str, Any] | None = None
    symptom_checks: list[SymptomCheck] = field(default_factory=list)
    injections: list[Treatment] = field(default_factory=list)
    recall_answers: dict[str, str | None] = field(default_factory=dict)
    alarm_events: list[AlarmEvent] = field(default_factory=list)

    @classmethod
    def load(cls, csv_path: str | Path) -> "Companion | None":
        p = Path(csv_path).with_suffix(".json")
        if not p.exists():
            return None
        raw = json.loads(p.read_text())
        return cls(
            scenario=raw.get("scenario", p.stem), kind=raw.get("kind", "core"), synthetic=bool(raw.get("synthetic", False)),
            # invariant 9: reason codes on history are inferred and labeled so, whatever the file says
            reason_codes={date.fromisoformat(d): (list(v["codes"]), "inferred")
                          for d, v in (raw.get("reason_codes") or {}).items()},
            plan=TitrationPlan.model_validate(raw["plan"]) if raw.get("plan") else None,
            dose_change=raw.get("dose_change") or None,
            symptom_checks=[SymptomCheck.model_validate(c) for c in raw.get("symptom_checks") or []],
            injections=[Treatment.model_validate(t) for t in raw.get("injections") or []],
            recall_answers=dict(raw.get("recall_answers") or {}),
            alarm_events=[AlarmEvent.model_validate(a) for a in raw.get("alarm_events") or []],
        )


@dataclass
class CatchUp:
    ledger: Ledger
    low_events: LowEventDetector
    recall: MorningRecall
    evaluate_night: Callable[[date], Awaitable[None]]  # main._evaluate_night: the watch, then Standing
    is_demo: Callable[[], bool] = lambda: True
    on_progress: Callable[[dict[str, Any]], None] | None = None
    on_last_night: Callable[[Any], None] | None = None  # the buddy's morning line, once, for the newest night

    # --- the companion, seeded once (every write is keyed, so a second seek writes nothing new) ---

    def seed(self, comp: Companion | None) -> dict[str, int]:
        if comp is None:
            return {}
        demo = self.is_demo()
        n = {"alarm_events": 0, "symptom_checks": 0, "injections": 0, "plan": 0, "dose_change": 0}
        for a in comp.alarm_events:
            store.upsert_alarm_event(a.model_copy(update={"is_demo": demo}))
            n["alarm_events"] += 1
        for c in comp.symptom_checks:
            if store.select_symptom_check(c.date, demo) is None:  # a tap on the kiosk during the demo wins
                store.upsert_symptom_check(c.model_copy(update={"is_demo": demo}))
                n["symptom_checks"] += 1
        if comp.injections:
            first = min(t.timestamp for t in comp.injections) - timedelta(days=1)
            have = {(t.timestamp, t.kind, t.dose_label) for t in store.select_treatments(first)}
            for t in comp.injections:
                if (t.timestamp, t.kind, t.dose_label) not in have:
                    store.insert_treatment(t, is_demo=demo)
                    n["injections"] += 1
        offered = any((d.get("message", {}).get("plan_id") == (comp.plan.plan_id if comp.plan else None))
                      for d in store.select_doctor_messages()) if comp.plan is not None else False
        # a plan offered through Spark enters only through the patient's confirm (invariant 8)
        if comp.plan is not None and not offered and not any(p.plan_id == comp.plan.plan_id for p in store.select_plans()):
            store.upsert_plan(comp.plan.model_copy(update={"status": "active", "is_demo": demo}))
            store.set_kv(f"step_watch:green:{comp.plan.plan_id}", "[]")
            n["plan"] = 1
        if comp.dose_change and store.get_kv(therapy_change_key(demo)) is None:
            store.set_kv(therapy_change_key(demo), str(comp.dose_change["date"]))
            n["dose_change"] = 1
        return n

    # --- the nights ---

    def first_night(self, first_row: datetime) -> date:
        """The night the scenario's first row belongs to (a row before the window
        end is the tail of the night that started the evening before)."""
        end_t = parse_hhmm(self.ledger.adapter.settings.night_window_end)
        return first_row.date() - timedelta(days=1) if first_row.time() < end_t else first_row.date()

    def nights_until(self, first_row: datetime, to: datetime) -> list[date]:
        """Every night whose window has closed by `to`, oldest first."""
        s = self.ledger.adapter.settings
        out = []
        d = self.first_night(first_row)
        while True:
            _, end = night_window(d, s.night_window_start, s.night_window_end)
            if end > to:
                break
            out.append(d)
            d += timedelta(days=1)
        return out

    def _overlay_codes(self, comp: Companion | None, night_date: date) -> None:
        if comp is None or night_date not in comp.reason_codes:
            return
        rec = store.select_night_record(night_date)
        if rec is None:
            return
        codes, source = comp.reason_codes[night_date]
        if rec.reason_codes != codes or rec.code_source != source:
            store.upsert_night_record(rec.model_copy(update={"reason_codes": codes, "code_source": source}))

    def _overlay_answers(self, comp: Companion | None, asked_at: datetime) -> int:
        if comp is None:
            return 0
        n = 0
        for low_id, answer in comp.recall_answers.items():
            if answer is None:
                continue  # null = no answer, never fine
            r = store.select_recall(low_id)
            if r is not None and r.answer is None and r.asked_at == asked_at:
                store.upsert_recall(r.model_copy(update={"answer": answer, "answered_at": asked_at + OVERLAY_ANSWER_DELAY}))
                n += 1
        return n

    async def run(self, first_row: datetime, to: datetime, comp: Companion | None = None) -> dict[str, Any]:
        """Replay every morning up to `to` that has not been replayed, then leave
        the clock at `to`. Returns what it did."""
        speed = clock.speed
        s = self.ledger.adapter.settings
        demo = self.is_demo()
        summary: dict[str, Any] = {"seeded": self.seed(comp), "nights_built": 0, "mornings_evaluated": 0, "answers_overlaid": 0, "to": to.isoformat()}
        saved_hook, self.ledger.on_record = self.ledger.on_record, None  # the mornings run here, in order, awaited
        landed = to
        try:
            for night_date in self.nights_until(first_row, to):
                _, end = night_window(night_date, s.night_window_start, s.night_window_end)
                landed = end  # a morning that fails leaves the clock before it, so the same seek can be retried
                clock.set(speed=speed, start=end + MORNING_OFFSET)
                existing = store.select_night_record(night_date)
                if existing is None or existing.is_demo != demo:
                    self.ledger.build_night(night_date)
                    self._overlay_codes(comp, night_date)
                    events = self.low_events.detect(night_date)
                    self.recall.create(night_date, events, asked_at=end)
                    summary["answers_overlaid"] += self._overlay_answers(comp, end)
                    summary["nights_built"] += 1
                await self.evaluate_night(night_date)  # idempotent: the budget sends nothing twice
                summary["mornings_evaluated"] += 1
                if self.on_progress is not None:
                    self.on_progress({"night_date": night_date.isoformat(), "to": to.isoformat()})
            landed = to
            last = self.nights_until(first_row, to)
            if last and self.on_last_night is not None:
                record = store.select_night_record(last[-1])
                if record is not None:
                    try:
                        self.on_last_night(record)
                    except Exception:
                        log.exception("last-night hook failed")
        finally:
            self.ledger.on_record = saved_hook
            clock.set(speed=speed, start=landed)
        log.info("catch-up to %s: %s", to.isoformat(), summary)
        return summary
