"""R14(a): step-week vigilance. For 7 days after each step-up (days 1-7 of
every step with index >= 1), when the active plan's
options.step_week_vigilance is on, the predicted-low threshold is raised by
options.vigilance_offset_mgdl; on day 8 it is back on its own. It only ever
RAISES the predicted-low threshold (more cautious) and never touches the
actual-low alarm, which reads Settings.low_threshold directly. The only
Rounds code that reaches the alarm threshold path, and it reaches it through
AlarmEngine.predicted_low_threshold() alone (install()); alarm.py is not
edited. The plan comes from StepWatch.active_plan(), which is world-scoped:
a demo plan never raises the live threshold, and vice versa."""

from __future__ import annotations

import logging
import math
from datetime import date
from typing import Callable

from ..alarm import PREDICTED_LOW_THRESHOLD_MGDL
from ..clock import clock
from ..contracts import Settings, TitrationPlan

VIGILANCE_DAYS = 7

_log = logging.getLogger("irin.rounds.vigilance")


def in_step_week(plan: TitrationPlan, today: date) -> bool:
    """Day 1..7 of any step-up (a step with index >= 1)."""
    return any(s.index >= 1 and 0 <= (today - s.planned_start).days < VIGILANCE_DAYS for s in plan.steps)


def effective_predicted_low_threshold(settings: Settings, plan: TitrationPlan | None, today: date) -> float:
    """The predicted-low threshold for `today`: the engine's base, plus the plan's
    offset during a step week. An offset that is not a positive finite number
    never lowers or breaks the threshold: the base stands. The actual-low
    threshold (settings.low_threshold) is never read or changed here."""
    base = PREDICTED_LOW_THRESHOLD_MGDL
    if plan is None or plan.status != "active" or not plan.options.step_week_vigilance:
        return base
    offset = plan.options.vigilance_offset_mgdl
    if not math.isfinite(offset) or offset <= 0:
        return base
    return base + offset if in_step_week(plan, today) else base


def install(engine, settings: Settings, active_plan: Callable[[], TitrationPlan | None]) -> None:
    """Point the engine's predicted_low_threshold() at the vigilance rule, read
    fresh on every forecast so it expires on its own. Any failure (the store,
    a bad plan) falls back to the base threshold: vigilance never breaks an alarm."""

    def threshold() -> float:
        try:
            return effective_predicted_low_threshold(settings, active_plan(), clock.now().date())
        except Exception:
            _log.exception("vigilance threshold failed; using the base")
            return PREDICTED_LOW_THRESHOLD_MGDL

    engine.predicted_low_threshold = threshold
