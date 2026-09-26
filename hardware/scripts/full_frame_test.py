"""Slavik step 2 check: every LED in the frame, colour cycle at 30 percent
while you wiggle both corner connectors; pass = no flicker, no dead LED.
Run BY HAND on the Pi (SPI enabled, repo venv):

    .venv/bin/python hardware/scripts/full_frame_test.py            # 29 LEDs, 30 %
    .venv/bin/python hardware/scripts/full_frame_test.py --count 29 --brightness 0.2

Brightness is capped at 30 percent: strip power crosses the breadboard
(docs/wiring.md "As built"), about 1 A max, and 29 LEDs at full white draw
about 1.7 A. The frame is always switched off on exit, Ctrl+C included.
Driver: Pi5Neo over /dev/spidev0.0 (GPIO10, physical pin 19)."""

from __future__ import annotations

import argparse
import time

MAX_BRIGHTNESS = 0.30

COLORS = [
    ("red", (255, 0, 0)),
    ("green", (0, 255, 0)),
    ("blue", (0, 0, 255)),
    ("white", (255, 255, 255)),
    ("amber", (255, 120, 0)),
]


def scale(rgb: tuple[int, int, int], brightness: float) -> tuple[int, int, int]:
    return tuple(int(c * brightness) for c in rgb)  # type: ignore[return-value]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--count", type=int, default=29, help="LEDs in the frame (default 29)")
    ap.add_argument("--brightness", type=float, default=MAX_BRIGHTNESS, help="0-0.30 (default 0.30)")
    ap.add_argument("--hold", type=float, default=3.0, help="seconds per colour (default 3)")
    ap.add_argument("--loops", type=int, default=3, help="colour cycles (default 3)")
    args = ap.parse_args()
    brightness = max(0.0, min(MAX_BRIGHTNESS, args.brightness))
    if brightness != args.brightness:
        print(f"brightness capped to {brightness:.2f}")

    try:
        from pi5neo import Pi5Neo
    except ImportError:
        raise SystemExit("pi5neo not installed: run on the Pi inside the repo venv (hardware/requirements.txt)")

    strip = Pi5Neo("/dev/spidev0.0", args.count, 800)
    try:
        # 1. chase: one LED at a time, so a dead LED or bad corner shows its index
        print(f"chase: LED 0 -> {args.count - 1}, dim white")
        r, g, b = scale((255, 255, 255), brightness)
        for i in range(args.count):
            strip.clear_strip()
            strip.set_led_color(i, r, g, b)
            strip.update_strip()
            print(f"  LED {i}", end="\r", flush=True)
            time.sleep(0.15)
        print()

        # 2. whole frame, colour cycle: wiggle both corner connectors now
        print(f"colour cycle at {brightness:.0%}: wiggle both corner connectors; watch for flicker")
        for loop in range(args.loops):
            for name, rgb in COLORS:
                print(f"  loop {loop + 1}/{args.loops}: {name}")
                strip.fill_strip(*scale(rgb, brightness))
                strip.update_strip()
                time.sleep(args.hold)
        print("done: every LED lit in every colour with no flicker = PASS")
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        strip.clear_strip()
        strip.update_strip()
        print("frame off")


if __name__ == "__main__":
    main()
