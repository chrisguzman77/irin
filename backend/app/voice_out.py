"""Step 11+: spoken echoes and clips on the Pi. ElevenLabs render (device
voice) -> mp3 -> ffmpeg -> wav into backend/sounds/generated/ (gitignored),
keyed by text hash and never re-rendered for the same text; played through
hal AFTER a tone, never instead of it (invariant 22). VOICE_BACKEND=none
renders nothing and the tones stand alone; a missing key never raises in
the alarm path."""

from __future__ import annotations

from pathlib import Path


def render(text: str) -> Path | None:
    """The cached wav for `text`, or None when VOICE_BACKEND=none. TODO step 11+."""
    raise NotImplementedError("step 11+: ElevenLabs render -> ffmpeg -> wav cache")
