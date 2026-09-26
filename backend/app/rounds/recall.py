"""R11: morning recall. Self-report is the clinical standard for hypoglycemia
awareness (Gold and Clarke), asked once in clinic about a vague multi-month
impression; Irin asks about ONE specific low the next morning with the curve
attached. At night-window end one LowEventRecall per LowEvent, capped at the
two deepest per morning, broadcast as recall_due; four answers
(felt_and_treated, woke_no_symptoms, dont_remember, was_awake); a low with
carbs logged within 30 min arrives pre-filled treated (the app asks only
about symptoms). Asked once, answerable until NOON of that morning on
clock.py; anything unanswered stays answer = None and is reported as "no
answer", never fine, never in the unfelt-low denominator (nights.py's
standing_window divides unfelt by answered). A late answer before noon
updates the record and re-runs the morning evaluation (idempotent: the
budget sends nothing twice)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Callable

from .. import store
from ..clock import clock
from ..contracts import LowEvent, LowEventRecall

log = logging.getLogger("irin.rounds.recall")

MAX_PER_MORNING = 2
ANSWER_UNTIL = time(12, 0)  # the first noon after the question was asked
ANSWER_UNTIL_HHMM = ANSWER_UNTIL.strftime("%H:%M")  # the scheduler's recall_close job reads this, so the two never drift


class RecallError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status, self.detail = status, detail


def deepest(events: list[LowEvent], n: int = MAX_PER_MORNING) -> list[LowEvent]:
    """The n lowest nadirs (earliest first on a tie)."""
    return sorted(events, key=lambda e: (e.nadir_mgdl, e.started_at))[:n]


def answer_deadline(asked_at: datetime) -> datetime:
    """The first ANSWER_UNTIL strictly after asked_at (a night window ending after
    noon, or a late boot, still gets its morning)."""
    d = datetime.combine(asked_at.date(), ANSWER_UNTIL)
    return d if d > asked_at else d + timedelta(days=1)


def is_open(recall: LowEventRecall, now: datetime) -> bool:
    return recall.answer is None and now < answer_deadline(recall.asked_at)


@dataclass
class MorningRecall:
    is_demo: Callable[[], bool] = lambda: False
    on_due: Callable[[dict[str, Any]], None] | None = None  # recall_due broadcast
    on_answer: Callable[[LowEventRecall, LowEvent], None] | None = None  # re-run the morning's evaluation
    clock_synced: Callable[[], bool] = lambda: True  # the Pi's wall clock is untrusted before NTP: no answers until then

    def create(self, night_date: date, events: list[LowEvent], asked_at: datetime | None = None) -> list[LowEventRecall]:
        """At night-window end (`asked_at` = the window end, so a night rebuilt
        days later is born closed and reads "no answer"): the night's questions,
        the two deepest lows. A recall that already exists (a rebuilt night, a
        restart) is kept with its answer; nothing is asked twice. An unanswered
        question about a low the rebuilt night no longer ranks (or no longer
        has) is withdrawn, so the cap holds."""
        asked_at = asked_at or clock.now()
        demo = self.is_demo()
        night = [e for e in events if e.night_date == night_date and e.is_demo == demo]
        chosen = deepest(night)
        keep = {e.low_event_id for e in chosen}
        night_ids = {e.low_event_id for e in night}
        known = {e.low_event_id for e in store.select_low_events(date.min)}
        for r in store.select_recalls(date.min):
            if r.answer is None and r.is_demo == demo and r.low_event_id not in keep and (r.low_event_id in night_ids or r.low_event_id not in known):
                store.delete_recall(r.low_event_id)
        rows: list[LowEventRecall] = []
        for e in chosen:
            existing = store.select_recall(e.low_event_id)
            if existing is None:
                existing = LowEventRecall(low_event_id=e.low_event_id, asked_at=asked_at, is_demo=demo)
                store.upsert_recall(existing)
            rows.append(existing)
        if rows:
            log.info("morning recall for %s: %d question(s)", night_date, len(rows))
        self._announce()
        return rows

    def _items(self, recalls: list[LowEventRecall]) -> list[dict[str, Any]]:
        out = []
        for r in recalls:
            e = store.select_low_event(r.low_event_id)
            if e is None:
                continue
            out.append({"recall": r.model_dump(mode="json"), "low_event": e.model_dump(mode="json"),
                        "prefill_treated": e.carbs_logged_within_30min, "answer_until": answer_deadline(r.asked_at).isoformat()})
        return out

    def pending(self, now: datetime | None = None) -> list[dict[str, Any]]:
        """This morning's open questions (asked today, unanswered, before noon), this world's."""
        now = now or clock.now()
        demo = self.is_demo()
        rows = [r for r in store.select_recalls(now.date()) if r.is_demo == demo and r.asked_at.date() == now.date() and is_open(r, now)]
        return self._items(sorted(rows, key=lambda r: r.asked_at))

    def morning(self, day: date | None = None) -> list[dict[str, Any]]:
        """Every question asked on `day` with its answer so far (the app shows answers after a reload)."""
        day = day or clock.now().date()
        demo = self.is_demo()
        rows = [r for r in store.select_recalls(day) if r.is_demo == demo and r.asked_at.date() == day]
        return self._items(sorted(rows, key=lambda r: r.asked_at))

    def status(self) -> dict[str, Any]:
        return {"recalls": self.pending()}

    def answer(self, low_event_id: str, answer: str) -> LowEventRecall:
        """PIN-gated from the app. 404 unknown; 409 after noon (the question has
        closed: "no answer" stands). An answer before noon may be changed."""
        recall = store.select_recall(low_event_id)
        if recall is None or recall.is_demo != self.is_demo():
            raise RecallError(404, "no such morning question")
        if not self.clock_synced():
            raise RecallError(409, "the clock is not set yet; try again in a minute")
        now = clock.now()
        if now >= answer_deadline(recall.asked_at):
            raise RecallError(409, "the question closed at noon; it is recorded as no answer")
        recall = recall.model_copy(update={"answer": answer, "answered_at": now})
        store.upsert_recall(recall)
        event = store.select_low_event(low_event_id)
        if self.on_answer is not None and event is not None:
            try:
                self.on_answer(recall, event)
            except Exception:
                log.exception("recall answer observer failed")
        self._announce()
        return recall

    def close(self, day: date | None = None) -> int:
        """The noon job: nothing to write (an unanswered question stays answer =
        None, reported as "no answer"); the open list empties and is announced."""
        day = day or clock.now().date()
        demo = self.is_demo()
        left = [r for r in store.select_recalls(day) if r.is_demo == demo and r.asked_at.date() == day and r.answer is None]
        if left:
            log.info("%d morning question(s) unanswered by noon: recorded as no answer", len(left))
        self._announce()
        return len(left)

    def _announce(self) -> None:
        if self.on_due is not None:
            try:
                self.on_due(self.status())
            except Exception:
                log.exception("recall_due observer failed")
