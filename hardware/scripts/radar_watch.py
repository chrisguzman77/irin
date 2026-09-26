"""Slavik step 4 check: print every GPIO17 transition with a timestamp, and a
live line with how long the current state has held. Run BY HAND on the Pi as
the kiosk user (Pi setup note 4), from the repo root:

    .venv/bin/python hardware/scripts/radar_watch.py

Pass = PRESENT holds with a motionless person in place (sitting still in bed,
or at arm's length from the device at the demo table), and it drops to empty
a few seconds after the room (or the demo zone) is empty. Ctrl+C stops.
Cross-check without Python: `watch -n 0.5 pinctrl get 17` (hi / lo).
A read error prints ERROR (None): that is what the backend sees as "no value".
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hardware.presence import Radar  # noqa: E402


def label(v: bool | None) -> str:
    return "PRESENT" if v is True else "empty" if v is False else "ERROR (None)"


def main() -> None:
    radar = Radar()
    last, since = radar.read(), time.monotonic()
    print(f"{datetime.now():%H:%M:%S}  start: {label(last)}")
    try:
        while True:
            v = radar.read()
            if v != last:
                held = time.monotonic() - since
                print(f"\n{datetime.now():%H:%M:%S}  {label(last)} -> {label(v)}  (after {held:.1f} s)")
                last, since = v, time.monotonic()
            print(f"  {label(v):<12} for {time.monotonic() - since:6.1f} s", end="\r", flush=True)
            time.sleep(0.1)
    except KeyboardInterrupt:
        print(f"\n{len(radar.transitions)} transitions recorded")
    finally:
        radar.close()


if __name__ == "__main__":
    main()
