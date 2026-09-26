"""Presence state machine (chris.md step 9). Consumes the RAW radar value
from hal (True / False / None) and produces PresenceState for output gating
in the hal layer ONLY (LEDs, speaker, display brightness). Alarm logic,
forecasting, logging, and the phone path never read this.

Rules (TODO step 9, tests/test_presence.py):
- Home on any detection, instantly.
- Away only on ~15 min sustained absence (AWAY_AFTER_MIN, on clock.py) AND
  outside the night window: radar absence alone NEVER sets Away at night.
- The manual toggle (Settings.presence_override, PIN-gated) always wins
  over every automatic input.
- A raw sample of None (mock not driven, hal error) is no evidence either
  way and never counts toward Away.
- Returning re-enables outputs immediately.
Rounds and Night Buddy read the RAW radar (hal.get_presence()), never
this state (R2), because the night rule pins the state to Home overnight.
"""

from __future__ import annotations

from .clock import clock
from .contracts import PresenceState, Settings

AWAY_AFTER_MIN = 15


class PresenceMachine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.state = PresenceState(mode="home", source="radar", since=clock.now())
        self._absent_since = None

    def sample(self, raw: bool | None) -> PresenceState:
        """Feed one raw radar sample. TODO step 9: the rules above."""
        raise NotImplementedError("step 9: presence rules (night rule, toggle override, None is no evidence)")

    def set_override(self, mode: str) -> PresenceState:
        """Settings.presence_override changed (auto | home | away). TODO step 9."""
        raise NotImplementedError("step 9: manual toggle always wins")

    def outputs_enabled(self) -> bool:
        return self.state.mode == "home"
