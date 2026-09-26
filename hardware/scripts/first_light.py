"""Slavik step 2 check, part 1: light the first 3 LEDs at 10 percent (red,
green, blue) for 10 seconds, then switch the frame off. Run BY HAND on the Pi:

    .venv/bin/python hardware/scripts/first_light.py

Pass = LED 0 red, LED 1 green, LED 2 blue, the rest dark. Colours swapped
(e.g. LED 0 green) means the strip's colour order differs from Pi5Neo's GRB;
tell Claude. Nothing at all: check SPI is on (ls /dev/spidev0.0), the data
wire at chip pin 2 (G7), and +5 V on the strip. Ctrl+C switches off too."""

from __future__ import annotations

import time

COUNT = 29
LEVEL = 0.10


def main() -> None:
    try:
        from pi5neo import Pi5Neo
    except ImportError:
        raise SystemExit("pi5neo not installed: run on the Pi inside the repo venv (hardware/requirements.txt)")

    strip = Pi5Neo("/dev/spidev0.0", COUNT, 800)
    v = int(255 * LEVEL)
    try:
        strip.clear_strip()
        strip.set_led_color(0, v, 0, 0)
        strip.set_led_color(1, 0, v, 0)
        strip.set_led_color(2, 0, 0, v)
        strip.update_strip()
        print("LED 0 red, LED 1 green, LED 2 blue at 10 % for 10 s")
        time.sleep(10)
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        strip.clear_strip()
        strip.update_strip()
        print("frame off")


if __name__ == "__main__":
    main()
