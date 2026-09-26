"""The hardware abstraction layer (Slavik's boundary file; the backend
imports get_hal() and nothing else). Change the interface only with
contracts-level care and a journal "Interface changes" note.

Interface:
  set_leds(state: str)                     ambient | off | warning | full | strobe | message_waiting | buddy_alert
  play_sound(name: str, volume: float)     alarm_soft | alarm_urgent | chirp | buddy_chime (backend/sounds/)
  stop_sound()
  get_presence() -> bool | None            RAW radar; None = no value (mock not driven, or read error)
  set_display_brightness(level: float)     0.0 - 1.0

get_hal() returns MockHAL unless IRIN_HW == "real". Real drivers live in
leds.py, sound.py, presence.py behind guarded imports. Presence gating of
outputs (LEDs, speaker, brightness) happens in this layer only; the alarm
engine never reads presence.
"""

from __future__ import annotations

import os
from typing import Protocol


class HAL(Protocol):
    def set_leds(self, state: str) -> None: ...
    def play_sound(self, name: str, volume: float) -> None: ...
    def stop_sound(self) -> None: ...
    def get_presence(self) -> bool | None: ...
    def set_display_brightness(self, level: float) -> None: ...


_hal: HAL | None = None


def get_hal() -> HAL:
    global _hal
    if _hal is None:
        if os.environ.get("IRIN_HW", "mock") == "real":
            from .real import RealHAL  # guarded: Pi-only libraries

            _hal = RealHAL()
        else:
            from .mock import MockHAL

            _hal = MockHAL()
    return _hal
