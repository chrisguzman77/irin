"""Tone playback with a software volume ramp through the kiosk user's
PipeWire session (shell out to aplay or pw-play; never open ALSA hw: devices
directly, Chromium holds them). Low alarms must reach full volume regardless
of quiet-hour settings (the engine decides; the driver never caps). Names:
alarm_soft, alarm_urgent, chirp, buddy_chime, from backend/sounds/."""

from __future__ import annotations

import subprocess
from pathlib import Path

SOUNDS_DIR = Path(__file__).resolve().parents[1] / "backend" / "sounds"
_proc: subprocess.Popen | None = None


def play(name: str, volume: float) -> None:
    raise NotImplementedError("slavik.md step 3: aplay/pw-play with a volume ramp")


def stop() -> None:
    global _proc
    if _proc is not None:
        _proc.terminate()
        _proc = None
