"""Synthesize the four sound files the driver plays by name (chris.md step 4).

  alarm_soft.wav   soft two-tone, ~2 s      (predicted-low warning)
  alarm_urgent.wav louder, faster, ~2 s     (actual low)
  chirp.wav        one short chime          (high / stale)
  buddy_chime.wav  a distinct three-note pattern (the brokered buddy call)

16-bit mono 44.1 kHz. numpy + the wave module only. Run once from backend/:
    python sounds/make_tones.py
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

RATE = 44100
OUT = Path(__file__).resolve().parent


def tone(freq: float, seconds: float, amp: float, attack: float = 0.01, release: float = 0.05) -> np.ndarray:
    n = int(RATE * seconds)
    t = np.arange(n) / RATE
    wave_ = np.sin(2 * np.pi * freq * t)
    env = np.ones(n)
    a, r = int(RATE * attack), int(RATE * release)
    if a:
        env[:a] = np.linspace(0, 1, a)
    if r:
        env[-r:] = np.linspace(1, 0, r)
    return (amp * wave_ * env).astype(np.float32)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(RATE * seconds), dtype=np.float32)


def write(name: str, samples: np.ndarray) -> None:
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    with wave.open(str(OUT / name), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())


def main() -> None:
    # soft two-tone: A4 / C#5, gentle, two slow pairs (~2 s)
    soft = np.concatenate([tone(440, 0.45, 0.35), silence(0.05), tone(554, 0.45, 0.35), silence(0.1)] * 2)
    write("alarm_soft.wav", soft)
    # urgent: louder, faster, higher (~2 s of 8 short pulses)
    urgent = np.concatenate([tone(880, 0.12, 0.9), silence(0.03), tone(1175, 0.12, 0.9), silence(0.03)] * 8)
    write("alarm_urgent.wav", urgent[: int(RATE * 2.0)])
    # chirp: one short chime
    write("chirp.wav", np.concatenate([tone(1320, 0.18, 0.5), silence(0.05)]))
    # buddy chime: a distinct rising three-note pattern (E5 G5 B5)
    buddy = np.concatenate([tone(659, 0.2, 0.5), silence(0.04), tone(784, 0.2, 0.5), silence(0.04), tone(988, 0.35, 0.5)])
    write("buddy_chime.wav", buddy)
    for f in ("alarm_soft.wav", "alarm_urgent.wav", "chirp.wav", "buddy_chime.wav"):
        print(f, (OUT / f).stat().st_size, "bytes")


if __name__ == "__main__":
    main()
