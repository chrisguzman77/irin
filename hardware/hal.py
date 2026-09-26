"""The hardware abstraction layer (Slavik's boundary file; the backend
imports get_hal() and nothing else). Change the interface only with
contracts-level care and a journal "Interface changes" note.

Interface:
  set_leds(state: str, color=None, brightness=None)
                                           state in LED_STATES; color (r, g, b) 0-255 and
                                           brightness 0.0 - 1.0 override the state's defaults
  play_sound(name: str, volume: float)     name in SOUND_NAMES (backend/sounds/); volume 0.0 - 1.0,
                                           never capped by the driver (the engine decides)
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

# The fixed vocabularies the backend refers to by name. Unknown names raise
# ValueError, so a typo fails a test instead of silently doing nothing.
LED_STATES = frozenset({"ambient", "off", "warning", "full", "strobe", "message_waiting", "buddy_alert"})
SOUND_NAMES = frozenset({"alarm_soft", "alarm_urgent", "chirp", "buddy_chime"})

Color = tuple[int, int, int]


class HAL(Protocol):
    def set_leds(self, state: str, color: Color | None = None, brightness: float | None = None) -> None: ...
    def play_sound(self, name: str, volume: float) -> None: ...
    def stop_sound(self) -> None: ...
    def get_presence(self) -> bool | None: ...
    def set_display_brightness(self, level: float) -> None: ...


def check_led_state(state: str) -> None:
    if state not in LED_STATES:
        raise ValueError(f"unknown LED state {state!r}; expected one of {sorted(LED_STATES)}")


def check_sound_name(name: str) -> None:
    if name not in SOUND_NAMES:
        raise ValueError(f"unknown sound {name!r}; expected one of {sorted(SOUND_NAMES)}")


def check_color(color: Color | None) -> None:
    if color is None:
        return
    if len(color) != 3 or not all(isinstance(c, int) and 0 <= c <= 255 for c in color):
        raise ValueError(f"color must be (r, g, b) ints 0-255, got {color!r}")


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


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


def reset_hal_for_test() -> None:
    """Drop the cached HAL so the next get_hal() builds a fresh one."""
    global _hal
    _hal = None
