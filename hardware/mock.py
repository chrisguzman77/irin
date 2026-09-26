"""MockHAL: logs every call; get_presence() returns None until
set_presence_for_test(bool) drives it, so tests and the demo panel own
presence explicitly (None = no value, never present or absent).

It enforces the same vocabulary as the real drivers (hal.LED_STATES,
hal.SOUND_NAMES) and never caps volume: a low alarm at 1.0 reaches the
"speaker" at 1.0 whatever the quiet hours say (the engine decides)."""

from __future__ import annotations

import logging

from .hal import Color, check_color, check_led_state, check_sound_name, clamp01

log = logging.getLogger("irin.hal.mock")


class MockHAL:
    def __init__(self) -> None:
        self.led_state = "off"
        self.led_color: Color | None = None
        self.led_brightness: float | None = None
        self.playing: str | None = None
        self.volume = 0.0
        self.brightness = 1.0
        self._presence: bool | None = None
        self.calls: list[tuple] = []

    def set_leds(self, state: str, color: Color | None = None, brightness: float | None = None) -> None:
        check_led_state(state)
        check_color(color)
        self.led_state = state
        self.led_color = tuple(color) if color is not None else None
        self.led_brightness = clamp01(brightness) if brightness is not None else None
        # plain calls log as ("set_leds", state), the shape backend tests assert on;
        # the overrides are appended only when given
        if color is None and brightness is None:
            self.calls.append(("set_leds", state))
        else:
            self.calls.append(("set_leds", state, self.led_color, self.led_brightness))
        log.info("LEDs -> %s color=%s brightness=%s", state, self.led_color, self.led_brightness)

    def play_sound(self, name: str, volume: float) -> None:
        check_sound_name(name)
        self.playing, self.volume = name, clamp01(volume)
        self.calls.append(("play_sound", name, self.volume))
        log.info("play %s @ %.2f", name, self.volume)

    def stop_sound(self) -> None:
        self.playing = None
        self.calls.append(("stop_sound",))
        log.info("stop sound")

    def get_presence(self) -> bool | None:
        return self._presence

    def set_presence_for_test(self, present: bool | None) -> None:
        if present is not None and not isinstance(present, bool):
            raise ValueError(f"presence must be True, False, or None, got {present!r}")
        self._presence = present
        self.calls.append(("set_presence_for_test", present))

    def set_display_brightness(self, level: float) -> None:
        self.brightness = clamp01(level)
        self.calls.append(("set_display_brightness", level))
        log.info("brightness -> %.2f", self.brightness)
