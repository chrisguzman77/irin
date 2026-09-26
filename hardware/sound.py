"""Tone playback with a software volume ramp through the kiosk user's
PipeWire session: shell out to pw-play (or aplay on the default device, which
PipeWire's ALSA plugin routes); never open ALSA hw: devices directly, Chromium
holds them. No pygame, no numpy: the stdlib wave and array modules scale the
samples.

Names (hal.SOUND_NAMES), files from backend/sounds/ (Chris's make_tones.py):
  alarm_soft    loops until stop()   ramps from 30 % of the target over 4 s
  alarm_urgent  loops until stop()   ramps from 50 % of the target over 1.5 s
  chirp         plays once
  buddy_chime   plays once
The ramp applies to the first pass only; every later loop is at the target.

Volume is a sample gain, 0.0 - 1.0, where 1.0 is the file's full level. The
driver never caps it: low alarms must reach full volume regardless of quiet
hours (invariant 3; the engine decides, not the driver). The system mixer is
not touched.

play() returns at once (a daemon thread runs the player), because the backend
calls it from its event loop; a new play() stops the previous sound first."""

from __future__ import annotations

import array
import io
import logging
import shutil
import subprocess
import sys
import tempfile
import threading
import wave
from pathlib import Path

from .hal import check_sound_name, clamp01

log = logging.getLogger("irin.hal.sound")

SOUNDS_DIR = Path(__file__).resolve().parents[1] / "backend" / "sounds"
LOOPING = frozenset({"alarm_soft", "alarm_urgent"})
RAMPS: dict[str, tuple[float, float]] = {  # name -> (seconds, starting fraction of the target)
    "alarm_soft": (4.0, 0.30),
    "alarm_urgent": (1.5, 0.50),
}
_CACHE_DIR = Path("/dev/shm" if Path("/dev/shm").is_dir() else tempfile.gettempdir()) / "irin-sound"


def render_wav(src: Path, volume: float, ramp_s: float = 0.0, ramp_from: float = 1.0) -> bytes:
    """The 16-bit wav at `src`, scaled to `volume`, with an optional linear
    ramp from ramp_from * volume up to volume over the first ramp_s seconds."""
    with wave.open(str(src), "rb") as w:
        params = w.getparams()
        raw = w.readframes(params.nframes)
    if params.sampwidth != 2:
        raise ValueError(f"{src.name}: expected 16-bit PCM, got {8 * params.sampwidth}-bit")
    samples = array.array("h", raw)
    if sys.byteorder == "big":
        samples.byteswap()
    volume = clamp01(volume)
    ramp_n = int(ramp_s * params.framerate) * params.nchannels
    for i in range(len(samples)):
        gain = volume
        if i < ramp_n:
            gain = volume * (ramp_from + (1.0 - ramp_from) * i / ramp_n)
        samples[i] = max(-32768, min(32767, int(samples[i] * gain)))
    if sys.byteorder == "big":
        samples.byteswap()
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setparams(params)
        w.writeframes(samples.tobytes())
    return out.getvalue()


def prepared(name: str, volume: float, ramped: bool) -> Path:
    """Render once, cache as a file the player command can open."""
    volume = clamp01(volume)
    ramp_s, ramp_from = RAMPS.get(name, (0.0, 1.0)) if ramped else (0.0, 1.0)
    path = _CACHE_DIR / f"{name}-{volume:.3f}-{'r' if ramp_s else 's'}.wav"
    if not path.exists():
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(render_wav(SOUNDS_DIR / f"{name}.wav", volume, ramp_s, ramp_from))
        tmp.replace(path)
    return path


def player_command(path: Path) -> list[str]:
    if shutil.which("pw-play"):
        return ["pw-play", str(path)]
    if shutil.which("aplay"):
        return ["aplay", "-q", str(path)]  # the default device (PipeWire's ALSA plugin), never hw:
    raise RuntimeError("neither pw-play nor aplay found (Pi: PipeWire and alsa-utils ship with Raspberry Pi OS)")


class Player:
    def __init__(self, name: str, volume: float, popen=subprocess.Popen) -> None:
        check_sound_name(name)
        self.name, self.volume = name, clamp01(volume)
        self._popen = popen
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._proc = None
        self.passes = 0

    def run(self) -> None:
        ramped = True
        while not self._stop.is_set():
            cmd = player_command(prepared(self.name, self.volume, ramped))
            with self._lock:
                if self._stop.is_set():
                    return
                self._proc = self._popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._proc.wait()
            self.passes += 1
            ramped = False
            if self.name not in LOOPING:
                return

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                self._proc.terminate()


_current: Player | None = None
_current_lock = threading.Lock()


def play(name: str, volume: float) -> None:
    global _current
    player = Player(name, volume)
    with _current_lock:
        if _current is not None:
            _current.stop()
        _current = player
    threading.Thread(target=_guarded, args=(player,), name=f"irin-sound-{name}", daemon=True).start()
    log.info("play %s @ %.2f", name, player.volume)


def _guarded(player: Player) -> None:
    try:
        player.run()
    except Exception:
        log.exception("sound %s failed", player.name)


def stop() -> None:
    global _current
    with _current_lock:
        if _current is not None:
            _current.stop()
            _current = None
