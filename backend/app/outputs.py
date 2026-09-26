"""The output gate (chris.md step 9, "output gating happens in the hal
layer"): wraps Slavik's HAL so that Away suppresses ROOM OUTPUTS ONLY (LEDs,
speaker, display brightness) and NOTHING else. The alarm engine talks to this
object exactly as it would to the HAL; it never checks presence itself.

While Away every requested output is remembered, not played; when presence
returns the last requested LED state, sound, and brightness are re-applied
at once, so a still-active alarm sounds the moment someone is back in the
room. get_presence() passes straight through: R2 and B2 read the raw radar.
"""

from __future__ import annotations


class GatedOutputs:
    def __init__(self, hal, presence) -> None:
        self.hal = hal
        self.presence = presence
        self._leds = "ambient"
        self._sound: tuple[str, float] | None = None
        self._brightness: float | None = None
        self._suppressed = False
        presence.on_change(self._on_presence)

    # --- the HAL interface the engine uses ---

    def set_leds(self, state: str) -> None:
        self._leds = state
        if self.presence.outputs_enabled():
            self.hal.set_leds(state)

    def play_sound(self, name: str, volume: float) -> None:
        self._sound = (name, volume)
        if self.presence.outputs_enabled():
            self.hal.play_sound(name, volume)

    def stop_sound(self) -> None:
        self._sound = None
        if self.presence.outputs_enabled():
            self.hal.stop_sound()

    def set_display_brightness(self, level: float) -> None:
        self._brightness = level
        if self.presence.outputs_enabled():
            self.hal.set_display_brightness(level)

    def get_presence(self):
        return self.hal.get_presence()  # raw, ungated, always

    # --- gating ---

    def _on_presence(self, state) -> None:
        if state.mode == "away":
            self._suppressed = True
            self.hal.stop_sound()
            self.hal.set_leds("off")
        elif self._suppressed:
            self._suppressed = False
            self.hal.set_leds(self._leds)
            if self._sound is not None:
                self.hal.play_sound(*self._sound)
            if self._brightness is not None:
                self.hal.set_display_brightness(self._brightness)
