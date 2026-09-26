"""R8: the Standing Cards engine over the device's own stores. At 07:05 (five
minutes after the ledger, on clock.py behind the NTP guard) it evaluates
Basal Check and Hypo Response over the last 14 nights; the red rule
(Hypo Response) re-runs when an alarm episode closes. Each evaluation
becomes a SignalCard only if the noise budget allows it, then goes through
CardSender (sealed to the paired peers of the card's world). Follow-up
starts from a confirmed dose change (R9) and lands with it."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable

from .. import store
from ..clock import clock
from ..contracts import Settings
from . import noise
from .cards import CardSender, assemble
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

    def _inputs(self, today: date) -> dict[str, Any]:
        since = today - timedelta(days=self.thresholds.window_nights)
        return {
            "night_records": store.select_night_records(since, today),
            "low_events": store.select_low_events(since, today),
            "recalls": store.select_recalls(since),
            "alarm_events": store.select_alarm_events(clock.now() - timedelta(days=self.thresholds.window_nights + 1)),
        }

    def evaluations(self, today: date) -> list[Evaluation]:
        inp = self._inputs(today)
        alarm_source = "inferred" if self.brain_only() else "measured"
        alarms = [] if self.brain_only() else inp["alarm_events"]
        out = [
            evaluate_basal_check(inp["night_records"], inp["low_events"], inp["recalls"], self.thresholds,
                                 alarm_events=alarms, alarm_source=alarm_source),
            evaluate_hypo_response(inp["night_records"], inp["low_events"], inp["recalls"], alarms, self.settings,
                                   self.thresholds, alarm_source=alarm_source),
        ]
        change = self.therapy_change_date()
        if change is not None and today >= change + timedelta(days=7):  # Follow-up (verify): from day 7 after a confirmed change
            w = self.thresholds.window_nights
            before = store.select_night_records(change - timedelta(days=w), change - timedelta(days=1))
            after_7 = store.select_night_records(change, change + timedelta(days=6))
            after_14 = store.select_night_records(change, min(today, change + timedelta(days=w - 1)))
            out.append(evaluate_follow_up(before, after_7, after_14, self.thresholds, alarm_source=alarm_source))
        return out

    @staticmethod
    def therapy_change_date() -> date | None:
        raw = store.get_kv("therapy_change_date")
        return date.fromisoformat(raw) if raw else None

    async def run(self, today: date | None = None, only: str | None = None, event_key: str | None = None) -> list[dict]:
        """Evaluate, budget, assemble, send. Returns one entry per evaluation with
        the budget's verdict and, when sent, the delivery result."""
        today = today or clock.now().date()
        history = noise.history_from_store(store.select_cards(limit=500))
        out = []
        for ev in self.evaluations(today):
            if only and ev.kind != only:
                continue
            verdict = noise.allow(ev.kind, "standing", ev.status, clock.now(), history,
                                  active_watch=self.active_watch(), event_key=event_key if ev.status == "red" else None)
            entry = {"kind": ev.kind, "status": ev.status, "flags": ev.flags, "headline": ev.headline,
                     "budget": verdict.reason, "sent": None}
            self.last[ev.kind] = entry
            if verdict.allowed and ev.period_start and ev.period_end:
                card = assemble(program="standing", kind=ev.kind, status=ev.status, flags=ev.flags, metrics=ev.metrics,
                                confidence=ev.confidence, period_start=ev.period_start, period_end=ev.period_end,
                                headline=ev.headline, device_id=self.device_id, is_demo=self.is_demo(),
                                source="irin_brain" if self.brain_only() else "irin_bedside", nights=ev.nights,
                                excluded_counts=ev.excluded_counts)
                try:
                    entry["sent"] = await self.sender.send(card)
                except Exception:
                    log.exception("card send failed for %s", ev.kind)
                    entry["sent"] = {"status": "failed"}
            out.append(entry)
        return out
