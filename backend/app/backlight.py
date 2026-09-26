"""The display backlight. Until now nothing called set_display_brightness, so
night mode only changed what the page draws and the panel stayed at full
brightness all night.

Rules:
- Full brightness by day (detail and morning modes), dim in the night window.
- Full brightness whenever a LOW alarm is sounding (pending, active,
  re-armed), night or not: an alarm is never shown on a dimmed screen.
  Acknowledged or over, the level goes back to the mode's.
- A room output only: it goes through GatedOutputs, so Away remembers the
  level and applies it on return (invariant 6). It never reads presence and
  never touches alarm logic: it OBSERVES alarm state through on_transition
  (observers only observe) and never raises into the alarm engine.
- Writes only on a change of target, so the panel is not rewritten every tick.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from .contracts import AlarmState

DAY_LEVEL = 1.0
NIGHT_LEVEL = 0.15
LOW_TRIGGERS = {"predicted_low", "actual_low"}
SOUNDING = {"pending", "active", "rearmed"}

log = logging.getLogger("irin.backlight")


class BacklightController:
    def __init__(self, outputs: Any, display_mode: Callable[[], str], alarm_state: Callable[[], AlarmState]) -> None:
        self.outputs = outputs
        self.display_mode = display_mode
        self.alarm_state = alarm_state
        self.level: float | None = None

    def target(self) -> float:
        a = self.alarm_state()
        if a.trigger_type in LOW_TRIGGERS and a.state in SOUNDING:
            return DAY_LEVEL
        return NIGHT_LEVEL if self.display_mode() == "night" else DAY_LEVEL

    def update(self, *_: Any) -> float | None:
        """Apply the target if it changed. Safe as an on_transition observer
        (takes and ignores the transition) and from any tick; never raises."""
        try:
            t = self.target()
            if t != self.level:
                self.outputs.set_display_brightness(t)
                self.level = t
        except Exception:
            log.exception("backlight update failed; the alarm path is unaffected")
        return self.level
