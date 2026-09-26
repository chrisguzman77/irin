"""LD2410B presence radar on GPIO17 (physical pin 11) via gpiozero with the
lgpio backend (RPi.GPIO does not work on Pi 5), pull_up=False (internal
pull-down: an unplugged radar reads low, never floats), a short bounce_time.
read() returns the RAW value True/False and None only on a read error, never
a guess. The Away decision lives in the backend (app/presence.py)."""

from __future__ import annotations

try:
    from gpiozero import DigitalInputDevice  # type: ignore
except ImportError:  # laptops
    DigitalInputDevice = None

GPIO_PIN = 17


class Radar:
    def __init__(self) -> None:
        if DigitalInputDevice is None:
            raise RuntimeError("gpiozero not installed: the real radar is Pi-only (IRIN_HW=real)")
        self.pin = DigitalInputDevice(GPIO_PIN, pull_up=False, bounce_time=0.05)

    def read(self) -> bool | None:
        try:
            return bool(self.pin.value)
        except Exception:
            return None
