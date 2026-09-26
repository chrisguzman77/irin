"""MockHAL: logs every call; get_presence() returns None until
set_presence_for_test(bool) drives it, so tests and the demo panel own
presence explicitly (None = no value, never present or absent)."""

from __future__ import annotations

import logging

log = logging.getLogger("irin.hal.mock")


class MockHAL:
    def __init__(self) -> None:
        self.led_state = "off"
        self.playing: str | None = None
        self.volume = 0.0
        self.brightness = 1.0
        self._presence: bool | None = None
        self.calls: list[tuple] = []

    def set_leds(self, state: str) -> None:
        self.led_state = state
        self.calls.append(("set_leds", state))
        log.info("LEDs -> %s", state)

    def play_sound(self, name: str, volume: float) -> None:
        self.playing, self.volume = name, volume
        self.calls.append(("play_sound", name, volume))
        log.info("play %s @ %.2f", name, volume)

    def stop_sound(self) -> None:
        self.playing = None
        self.calls.append(("stop_sound",))
        log.info("stop sound")

    def get_presence(self) -> bool | None:
        return self._presence

    def set_presence_for_test(self, present: bool | None) -> None:
        self._presence = present
        self.calls.append(("set_presence_for_test", present))

    def set_display_brightness(self, level: float) -> None:
        self.brightness = max(0.0, min(1.0, level))
        self.calls.append(("set_display_brightness", level))
        log.info("brightness -> %.2f", self.brightness)
