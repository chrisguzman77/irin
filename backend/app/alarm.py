"""The tiered alarm state machine (chris.md step 4). Skeleton: the states,
the transitions, and the REAL on_transition observer registry that R2 (the
AlarmEvent recorder) and B2 (the buddy rung) hang off. alarm.py never reads
presence and is never edited by Rounds or Night Buddy work, except R14(a)
step-week vigilance, which may only raise the predicted-low threshold.

States: idle | pending | active | acknowledged | rearmed
Trigger types: predicted_low | actual_low | high | stale

  Idle -> Pending:        N = 2 consecutive forecasts below the low threshold.
  Pending -> Active:      actual value crosses threshold, OR 5 min unacknowledged.
  Pending -> Idle:        warning acknowledged (final for the episode), or the
                          forecast back above threshold for 2 consecutive readings.
  Active -> Acknowledged: user ack.
  Acknowledged -> Rearmed: still below threshold 15 min later; Rearmed behaves
                          as Active; repeats every 15 min until back above -> Idle.
  Episode ends after 2 recovered readings; a later crossing is a NEW episode.
  Acknowledging a WARNING never pre-acknowledges the actual low.
  Priority: actual_low > predicted_low > stale > high; a higher priority replaces
  a lower one immediately and does NOT inherit its acknowledgment.
  Stale during a low: the low keeps sounding, a stale banner is added.
  Highs: ONE-SHOT (chime, amber tint, persistent indicator); no ack, no re-arm;
  respect quiet hours. ONLY lows override quiet hours (invariant 3).
All timers on clock.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from .clock import clock
from .contracts import AlarmState, AlarmStateName, AlarmTrigger, Forecast, Reading, Settings


@dataclass(frozen=True)
class Transition:
    old_state: AlarmStateName
    new_state: AlarmStateName
    trigger_type: AlarmTrigger | None
    reading: Reading | None
    at: datetime


TransitionObserver = Callable[[Transition], None]


class AlarmEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.state = AlarmState()
        self._observers: list[TransitionObserver] = []

    # --- the ONE observer hook (real) ---

    def on_transition(self, callback: TransitionObserver) -> Callable[[], None]:
        """Register an observer; returns an unregister function. Observers only
        observe: they never call back into the engine."""
        self._observers.append(callback)

        def unregister() -> None:
            if callback in self._observers:
                self._observers.remove(callback)

        return unregister

    def _transition(self, new_state: AlarmStateName, trigger_type: AlarmTrigger | None,
                    reading: Reading | None = None) -> None:
        old = self.state.state
        now = clock.now()
        started = self.state.started_at if new_state != "idle" and old != "idle" else (now if new_state != "idle" else None)
        self.state = AlarmState(state=new_state, trigger_type=trigger_type if new_state != "idle" else None,
                                started_at=started,
                                acknowledged_at=now if new_state == "acknowledged" else None)
        t = Transition(old, new_state, trigger_type, reading, now)
        for cb in list(self._observers):
            cb(t)

    # --- inputs (TODO step 4) ---

    def process_reading(self, reading: Reading) -> None:
        raise NotImplementedError("step 4: actual-low / high / stale transitions")

    def process_forecast(self, forecast: Forecast) -> None:
        raise NotImplementedError("step 4: N consecutive predicted lows -> pending")

    def acknowledge(self, source: str) -> None:
        raise NotImplementedError("step 4: ack; warning ack is final, full ack re-arms after 15 min")
