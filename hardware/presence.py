"""LD2410B presence radar on GPIO17 (physical pin 11) via gpiozero with the
lgpio backend (RPi.GPIO does not work on Pi 5), pull_up=False (internal
pull-down: an unplugged radar reads low, never floats), a short bounce_time
as the debounce.

read() returns the RAW value the radar sees: True / False, and None only on a
read error, never a guess and never the backend's Home/Away state (Rounds
samples it every 30 s during alarms). The Away decision lives in the backend
(app/presence.py).

Every raw change is recorded with a timestamp (transitions, last_change) so
radar_watch.py and diagnostics can show what the radar did and when. The
timestamps are the Pi's wall clock for display only; nothing decides on them.
Radar range and the no-one duration live in the radar itself (HLK app,
docs/plans/slavik.md step 4: HOME and DEMO gate profiles)."""

from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime

log = logging.getLogger("irin.hal.presence")

try:
    from gpiozero import DigitalInputDevice  # type: ignore
except ImportError:  # laptops
    DigitalInputDevice = None

GPIO_PIN = 17
BOUNCE_S = 0.05
HISTORY = 200


class Radar:
    """`device` is injectable: tests pass a fake with .value and the
    when_activated / when_deactivated hooks gpiozero provides."""

    def __init__(self, device=None, now=datetime.now) -> None:
        if device is None:
            if DigitalInputDevice is None:
                raise RuntimeError("gpiozero not installed: the real radar is Pi-only (IRIN_HW=real)")
            device = DigitalInputDevice(GPIO_PIN, pull_up=False, bounce_time=BOUNCE_S)
        self.pin = device
        self._now = now
        self._lock = threading.Lock()
        self.transitions: deque[tuple[datetime, bool]] = deque(maxlen=HISTORY)
        self.last_change: datetime | None = None
        self._last: bool | None = None
        self.pin.when_activated = lambda *_: self._record(True)
        self.pin.when_deactivated = lambda *_: self._record(False)
        self._record(self.read())

    def _record(self, value: bool | None) -> None:
        if value is None:
            return
        with self._lock:
            if value == self._last:
                return
            ts = self._now()
            self._last, self.last_change = value, ts
            self.transitions.append((ts, value))
        log.info("radar %s at %s", "PRESENT" if value else "empty", ts.isoformat(timespec="seconds"))

    def read(self) -> bool | None:
        try:
            value = self.pin.value
        except Exception:
            log.warning("radar read failed", exc_info=True)
            return None
        if value is None:
            return None
        return bool(value)

    def close(self) -> None:
        try:
            self.pin.close()
        except Exception:
            pass
