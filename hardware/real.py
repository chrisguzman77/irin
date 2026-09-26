"""RealHAL: composes leds.py, sound.py, presence.py. Imported only when
IRIN_HW=real (Pi only).

Each device starts independently: a dead LED frame, radar, or backlight is logged and
that device's calls become no-ops, but the others keep working, so a broken
strip can never silence a low alarm (invariant 3). A radar that failed to
start reads None ("no value"), never a guess."""

from __future__ import annotations

import logging
from pathlib import Path

from . import leds, presence, sound
from .hal import Color, check_led_state, check_sound_name, clamp01

log = logging.getLogger("irin.hal.real")

BACKLIGHT_ROOT = Path("/sys/class/backlight")


class Backlight:
    """The DSI panel's backlight: writes level x max_brightness to
    /sys/class/backlight/<name>/brightness. Needs the udev rule from Pi setup
    note 2 (group video, g+w) because the backend never runs as root. A
    missing panel or permission is logged, never raised: brightness is a
    room output and must never take the alarm path down."""

    def __init__(self, root: Path = BACKLIGHT_ROOT) -> None:
        devices = sorted(root.glob("*/brightness")) if root.is_dir() else []
        if not devices:
            raise RuntimeError(f"no backlight under {root}")
        self.path = devices[0]
        self.max = int((self.path.parent / "max_brightness").read_text().strip())

    def set(self, level: float) -> None:
        value = int(round(clamp01(level) * self.max))
        try:
            self.path.write_text(f"{value}\n")
        except OSError:
            log.exception("backlight write failed (udev rule from Pi setup note 2 installed?)")


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
        self.backlight = _start("backlight", Backlight)

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
        if self.backlight is not None:
            self.backlight.set(level)
