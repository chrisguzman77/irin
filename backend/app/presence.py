"""Presence state machine (chris.md step 9). Consumes the RAW radar value
from hal (True / False / None) and produces PresenceState for output gating
(app/outputs.py: LEDs, speaker, display brightness ONLY). Alarm logic,
forecasting, logging, and the phone path never read this (invariant 6).

Rules:
- Home on any detection, instantly.
- Away only on AWAY_AFTER_MIN (15) of sustained absence, timed on clock.py,
  AND outside the night window: radar absence alone NEVER sets Away at night.
- The manual toggle (Settings.presence_override: home | away) always wins
  over every automatic input; "auto" hands control back to the radar.
- A raw sample of None (mock not driven, hal error) is no evidence either
  way and never counts toward Away.
- Returning re-enables outputs immediately (outputs.py replays the last
  requested state, so a still-active alarm sounds the moment presence
  returns).
Because the night rule pins the STATE to Home overnight, it is useless as
evidence that a person was in the room at 3 AM; Rounds and Night Buddy read
the RAW radar from hal (R2), never this state.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable

from .clock import clock
from .contracts import PresenceState, Settings  # noqa: F401  (re-exported for main.py)
from .windows import in_window

AWAY_AFTER_MIN = 15
SAMPLE_CLOCK_SECONDS = 30.0

PresenceObserver = Callable[[PresenceState], None]


class PresenceMachine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.state = PresenceState(mode="home", source="radar", since=clock.now())
        self._absent_since: datetime | None = None
        self._observers: list[PresenceObserver] = []

    def on_change(self, callback: PresenceObserver) -> None:
        self._observers.append(callback)

    def _set(self, mode: str, source: str) -> None:
        if self.state.mode == mode and self.state.source == source:
            return
        self.state = PresenceState(mode=mode, source=source, since=clock.now())  # type: ignore[arg-type]
        for cb in list(self._observers):
            cb(self.state)

    def outputs_enabled(self) -> bool:
        return self.state.mode == "home"

    def set_override(self, mode: str) -> PresenceState:
        """Settings.presence_override changed: auto | home | away. The toggle
        always wins; auto returns control to the radar on the next sample."""
        if mode not in ("auto", "home", "away"):
            raise ValueError("presence_override must be auto, home, or away")
        self.settings.presence_override = mode  # type: ignore[assignment]
        if mode == "auto":
            self._absent_since = None
            self._set("home", "radar")  # start from Home; the radar decides from here
        else:
            self._set(mode, "toggle")
        return self.state

    def sample(self, raw: bool | None) -> PresenceState:
        """Feed one raw radar sample."""
        if self.settings.presence_override != "auto":
            self._set(self.settings.presence_override, "toggle")
            return self.state
        now = clock.now()
        if raw is True:
            self._absent_since = None
            self._set("home", "radar")
        elif raw is False:
            if self._absent_since is None:
                self._absent_since = now
            night = in_window(now.time(), self.settings.night_window_start, self.settings.night_window_end)
            if not night and now - self._absent_since >= timedelta(minutes=AWAY_AFTER_MIN):
                self._set("away", "radar")
        # raw is None: no evidence, nothing changes
        return self.state
