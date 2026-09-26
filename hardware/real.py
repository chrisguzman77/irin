"""RealHAL: composes leds.py, sound.py, presence.py. Imported only when
IRIN_HW=real (Pi only).

Each device starts independently: a dead LED frame or radar is logged and
that device's calls become no-ops, but the others keep working, so a broken
strip can never silence a low alarm (invariant 3). A radar that failed to
start reads None ("no value"), never a guess."""

from __future__ import annotations

import logging

from . import leds, presence, sound
from .hal import Color, check_led_state, check_sound_name

log = logging.getLogger("irin.hal.real")


def _start(name: str, factory):
    try:
        return factory()
    except Exception:
        log.exception("%s failed to start; its outputs are disabled, everything else runs", name)
        return None


class RealHAL:
    def __init__(self) -> None:
        self.leds = _start("LED frame", leds.LedFrame)
        self.radar = _start("radar", presence.Radar)

    def set_leds(self, state: str, color: Color | None = None, brightness: float | None = None) -> None:
        check_led_state(state)
        if self.leds is not None:
            self.leds.set_state(state, color, brightness)

    def play_sound(self, name: str, volume: float) -> None:
        check_sound_name(name)
        try:
            sound.play(name, volume)
        except Exception:
            log.exception("sound %s failed to play", name)

    def stop_sound(self) -> None:
        sound.stop()

    def get_presence(self) -> bool | None:
        return self.radar.read() if self.radar is not None else None

    def set_display_brightness(self, level: float) -> None:
        raise NotImplementedError("Pi: write /sys/class/backlight/*/brightness (udev rule in deploy/install.sh)")
