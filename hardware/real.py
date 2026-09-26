"""RealHAL: composes leds.py, sound.py, presence.py. Imported only when
IRIN_HW=real (Pi only)."""

from __future__ import annotations

from . import leds, presence, sound


class RealHAL:
    def __init__(self) -> None:
        self.leds = leds.LedFrame()
        self.radar = presence.Radar()

    def set_leds(self, state: str) -> None:
        self.leds.set_state(state)

    def play_sound(self, name: str, volume: float) -> None:
        sound.play(name, volume)

    def stop_sound(self) -> None:
        sound.stop()

    def get_presence(self) -> bool | None:
        return self.radar.read()

    def set_display_brightness(self, level: float) -> None:
        raise NotImplementedError("Pi: write /sys/class/backlight/*/brightness (udev rule in deploy/install.sh)")
