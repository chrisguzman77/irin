"""WS2812B frame over SPI (GPIO10 / MOSI, physical pin 19) through the
SN74AHCT125N level shifter (docs/wiring.md). Pi5Neo or Blinka SPI NeoPixel;
rpi_ws281x does NOT work on Pi 5. States: ambient (low brightness), off
(night), warning (amber ramp), full (red pulse), strobe (escalation), and the
optional later message_waiting / buddy_alert (never during an alarm, never at
night). The strip's 5V comes ONLY from the ALITOVE supply (invariant 4)."""

from __future__ import annotations

try:
    from pi5neo import Pi5Neo  # type: ignore
except ImportError:  # laptops
    Pi5Neo = None


class LedFrame:
    def __init__(self, count: int = 29) -> None:  # the as-built frame (docs/wiring.md)
        if Pi5Neo is None:
            raise RuntimeError("pi5neo not installed: real LEDs are Pi-only (IRIN_HW=real)")
        self.strip = Pi5Neo("/dev/spidev0.0", count, 800)

    def set_state(self, state: str, color: tuple[int, int, int] | None = None, brightness: float | None = None) -> None:
        raise NotImplementedError("slavik.md step 2: LED states")
