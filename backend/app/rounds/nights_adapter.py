"""R3: the adapter between the device's stores and ml/models/nights.py. It
gathers one night's inputs (readings with 3 h before and 2 h after the
window as context, treatments, alarm events, the presence toggle history)
and hands them to George's classify_night / night_metrics / low_events;
it never re-implements a metric or a reason code.

Under IRIN_BRAIN_ONLY (or the demo panel's toggle) the adapter passes
treatments=None (glucose-only inference, code_source "inferred"), no alarm
hardware events, and no presence, so basal_late, exercise, and away are
unavailable: rows change confidence label, they are never blanked (R14c)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Callable

from ..contracts import AlarmEvent, PresenceState, Reading, Settings, Treatment
from ..windows import parse_hhmm

CONTEXT_BEFORE = timedelta(hours=3)  # late meals and corrections before the night (nights.py's 3 h rules)

try:
    from ml.models.nights import BASAL_LATE_MIN as _BASAL_LATE_MIN, EXERCISE_AFTER as EXERCISE_FROM
except Exception:  # ml/ missing: the fetch bounds fall back to the documented values
    EXERCISE_FROM, _BASAL_LATE_MIN = time(17, 0), 60
BASAL_SEARCH = timedelta(hours=3)  # nights._basal_codes looks for a basal from usual - 3 h to the window end
CONTEXT_AFTER = timedelta(hours=2)  # recovery slopes and rebounds after the window end

Rows = Callable[[datetime, datetime], list[Any]]


def night_window(night_date: date, night_start: str, night_end: str) -> tuple[datetime, datetime]:
    """Rounds keys a night by the EVENING it starts on (nights.py and the
    fixtures agree: night_date = window_start.date()); the morning report keys
    by the morning it ends on. This is the one place that converts."""
    a, b = parse_hhmm(night_start), parse_hhmm(night_end)
    start = datetime.combine(night_date, a)
    end = datetime.combine(night_date + timedelta(days=1) if b <= a else night_date, b)
    return start, end


def night_ended_on(morning: date, night_start: str, night_end: str) -> date:
    """The night_date of the night that ended this morning (the scheduler fires with the morning)."""
    return morning - timedelta(days=1) if parse_hhmm(night_end) <= parse_hhmm(night_start) else morning


@dataclass
class NightInputs:
    night_date: date
    window: tuple[datetime, datetime]
    readings: list[Reading]
    treatments: list[Treatment] | None
    alarm_events: list[AlarmEvent]
    presence: list[PresenceState]
    usual_basal_time: str | None
    brain_only: bool


@dataclass
class NightsAdapter:
    settings: Settings
    readings_for: Rows  # (start, end) -> readings, non-stale rows only matter downstream
    treatments_for: Rows
    alarm_events_for: Rows
    presence_for: Rows  # the toggle history (PresenceState transitions) overlapping [start, end]
    brain_only: Callable[[], bool] = lambda: False

    def night_inputs(self, night_date: date) -> NightInputs:
        start, end = night_window(night_date, self.settings.night_window_start, self.settings.night_window_end)
        brain = self.brain_only()
        readings = [r for r in self.readings_for(start - CONTEXT_BEFORE, end + CONTEXT_AFTER) if not r.is_stale]
        # fetch bounds only (the rules live in nights.py): 3 h before for meals and corrections, 17:00 for
        # exercise, the usual basal time minus its search window, and 2 h after for carbs that treat a
        # low still running at the window end
        treatments_from = min(start - CONTEXT_BEFORE, datetime.combine(start.date(), EXERCISE_FROM))
        if self.settings.basal_time and not brain:
            treatments_from = min(treatments_from,
                                  datetime.combine(start.date(), parse_hhmm(self.settings.basal_time)) - BASAL_SEARCH)
        return NightInputs(
            night_date=night_date, window=(start, end), readings=readings,
            treatments=None if brain else list(self.treatments_for(treatments_from, end + CONTEXT_AFTER)),
            alarm_events=[] if brain else list(self.alarm_events_for(start - CONTEXT_BEFORE, end)),
            presence=[] if brain else list(self.presence_for(start, end)),
            usual_basal_time=None if brain else self.settings.basal_time, brain_only=brain,
        )

    # --- George's functions, called with the contract objects (nights.py is duck-typed) ---

    @staticmethod
    def classify(inp: NightInputs) -> tuple[list[str], str]:
        from ml.models.nights import classify_night

        return classify_night(inp.readings, inp.treatments, inp.alarm_events if inp.treatments is not None else None,
                              inp.presence or None, inp.window, usual_basal_time=inp.usual_basal_time)

    @staticmethod
    def metrics(inp: NightInputs) -> dict:
        from ml.models.nights import night_metrics

        return night_metrics(inp.readings, inp.alarm_events, inp.window)

    @staticmethod
    def lows(inp: NightInputs) -> list[dict]:
        from ml.models.nights import low_events

        return low_events(inp.readings, inp.treatments, inp.alarm_events, inp.window)
