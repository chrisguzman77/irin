"""R8: the Standing Cards engine over the device's own stores. At 07:05 (five
minutes after the ledger, on clock.py behind the NTP guard) it evaluates
Basal Check and Hypo Response over the last 14 nights; the red rule
(Hypo Response) re-runs when an alarm episode closes. Each evaluation
becomes a SignalCard only if the noise budget allows it, then goes through
CardSender (sealed to the paired peers of the card's world). Follow-up
starts from a confirmed dose change (R9) and lands with it."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Callable

from .. import store
from ..clock import clock
from ..contracts import Settings
from . import noise
from .cards import CardSender, assemble, with_narrative
from .resources import categories_for
from .standing import Evaluation, Thresholds, evaluate_basal_check, evaluate_follow_up, evaluate_hypo_response

log = logging.getLogger("irin.rounds.evaluate")


@dataclass
class StandingEngine:
    settings: Settings
    sender: CardSender
    device_id: str
    is_demo: Callable[[], bool] = lambda: False
    brain_only: Callable[[], bool] = lambda: False
    active_watch: Callable[[], bool] = lambda: False  # R10: a Step Watch suspends Standing Cards
    thresholds: Thresholds = field(default_factory=Thresholds)
    last: dict[str, Any] = field(default_factory=dict)  # the latest evaluations, for the panel
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def window(self, today: date) -> tuple[date, date]:
        """The 14 night_dates ending on `today` (the evening date of the newest night)."""
        return today - timedelta(days=self.thresholds.window_nights - 1), today

    def _inputs(self, today: date) -> dict[str, Any]:
        """The window's rows of THIS world only: demo nights never feed a real card and
        vice versa (invariants 1, 11). Alarm episodes by the window's clock span, not now."""
        since, until = self.window(today)
        demo = self.is_demo()
        start = datetime.combine(since, time(12, 0))
        end = datetime.combine(until + timedelta(days=1), time(12, 0))
        return {
            "night_records": [r for r in store.select_night_records(since, until) if r.is_demo == demo],
            "low_events": [e for e in store.select_low_events(since, until) if e.is_demo == demo],
            "recalls": [r for r in store.select_recalls(since) if r.is_demo == demo],
            "alarm_events": [a for a in store.select_alarm_events(start, end) if a.is_demo == demo],
        }

    def evaluations(self, today: date) -> list[Evaluation]:
        inp = self._inputs(today)
        alarm_source = "inferred" if self.brain_only() else "measured"
        alarms = inp["alarm_events"]  # brain_only changes the label to inferred, never blanks the counts
        out = [
            evaluate_basal_check(inp["night_records"], inp["low_events"], inp["recalls"], self.thresholds,
                                 alarm_events=alarms, alarm_source=alarm_source),
            evaluate_hypo_response(inp["night_records"], inp["low_events"], inp["recalls"], alarms, self.settings,
                                   self.thresholds, alarm_source=alarm_source),
        ]
        change = self.therapy_change_date()
        w = self.thresholds.window_nights
        # Follow-up (verify): once the first 7 nights after the change are in, until the 14th; then it rests
        if change is not None and change + timedelta(days=6) <= today <= change + timedelta(days=w - 1):
            demo = self.is_demo()
            def nights(a, b):
                return [r for r in store.select_night_records(a, b) if r.is_demo == demo]
            before = nights(change - timedelta(days=w), change - timedelta(days=1))
            after_7 = nights(change, change + timedelta(days=6))
            after_14 = nights(change, min(today, change + timedelta(days=w - 1)))
            out.append(evaluate_follow_up(before, after_7, after_14, self.thresholds, alarm_source=alarm_source))
        return out

    def therapy_change_date(self) -> date | None:
        from .messages import therapy_change_key

        raw = store.get_kv(therapy_change_key(self.is_demo()))
        return date.fromisoformat(raw) if raw else None

    @staticmethod
    def _red_event_key(inp: dict[str, Any]) -> str | None:
        """A red is keyed to the newest low episode in the window, so the 07:05
        run and the on-close run deduplicate to one card per episode."""
        lows = [a for a in inp["alarm_events"] if a.tier in ("predicted_low", "actual_low")]
        return max(lows, key=lambda a: a.started_at).event_id if lows else None

    async def run(self, today: date | None = None, only: str | None = None, event_key: str | None = None) -> list[dict]:
        """Evaluate, budget, assemble, send: one run at a time (two episodes closing
        within a relay round trip must not both pass the red cap). Returns one entry
        per evaluation with the budget's verdict and, when sent, the delivery result."""
        async with self._lock:
            return await self._run(today, only, event_key)

    async def _run(self, today: date | None, only: str | None, event_key: str | None) -> list[dict]:
        today = today or clock.now().date()
        since, until = self.window(today)
        inp = self._inputs(today)
        history = noise.history_from_store(store.select_cards(limit=500))
        out = []
        for ev in self.evaluations(today):
            if only and ev.kind != only:
                continue
            key = (event_key or self._red_event_key(inp)) if ev.status == "red" else None
            verdict = noise.allow(ev.kind, "standing", ev.status, clock.now(), history,
                                  active_watch=self.active_watch(), event_key=key)
            entry = {"kind": ev.kind, "status": ev.status, "flags": ev.flags, "headline": ev.headline,
                     "budget": verdict.reason, "sent": None}
            self.last[ev.kind] = entry
            if verdict.allowed:
                card = assemble(program="standing", kind=ev.kind, status=ev.status, flags=ev.flags, metrics=ev.metrics,
                                confidence=ev.confidence, period_start=ev.period_start or since,
                                period_end=ev.period_end or until, headline=ev.headline, device_id=self.device_id,
                                is_demo=self.is_demo(), source="irin_brain" if self.brain_only() else "irin_bedside",
                                nights=ev.nights, excluded_counts=ev.excluded_counts,
                                resource_categories=categories_for(ev.kind, ev.flags))
                card = await with_narrative(card)
                try:
                    entry["sent"] = await self.sender.send(card, event_key=key)
                except Exception:
                    log.exception("card send failed for %s", ev.kind)
                    entry["sent"] = {"status": "failed"}
                history.append(noise.Sent(ev.kind, "standing", ev.status, clock.now(), key))
            out.append(entry)
        return out
