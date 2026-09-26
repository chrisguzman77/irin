"""Spoken clips on the Pi (step 11+, F3): ElevenLabs renders `text` in the
device voice ONCE per text hash into backend/sounds/generated/ (gitignored:
clips can carry names) and never re-renders the same text. VOICE_BACKEND=none
(the default on every laptop) renders nothing; a missing key or a failed
call returns None and never raises, so nothing in the morning-report or
alarm path can be taken down by the voice (invariant 22: a clip is always
additive, played after a tone, never instead of one).

The wav conversion for hal playback of spoken echoes lands with step 11+'s
device echoes; the family story only needs the mp3 (email attachment, and
the morning screen's player)."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from .config import BACKEND_DIR, config

log = logging.getLogger("irin.voice_out")

CLIPS_DIR = BACKEND_DIR / "sounds" / "generated"
MODEL_ID = "eleven_multilingual_v2"


def clip_path(text: str) -> Path:
    return CLIPS_DIR / f"{hashlib.sha256(text.encode()).hexdigest()[:24]}.mp3"


def render(text: str, voice_id: str | None = None) -> Path | None:
    """The cached mp3 for `text`, rendering it on a miss; None when the voice
    backend is off, unconfigured, or failing."""
    if config.VOICE_BACKEND != "elevenlabs" or not text.strip():
        return None
    voice = voice_id or config.ELEVENLABS_VOICE_DEVICE
    if not config.ELEVENLABS_API_KEY or not voice:
        log.info("voice backend elevenlabs without a key or voice id; no clip")
        return None
    path = clip_path(text)
    if path.exists():
        return path
    try:
        from elevenlabs.client import ElevenLabs

        client = ElevenLabs(api_key=config.ELEVENLABS_API_KEY, timeout=30.0)
        audio = b"".join(client.text_to_speech.convert(voice, text=text, model_id=MODEL_ID,
                                                       output_format="mp3_44100_64"))
        if not audio:
            raise RuntimeError("empty audio")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(audio)
        tmp.replace(path)
        return path
    except Exception as e:  # network, quota, bad voice id: the text still ships
        log.warning("voice render failed (%s); no clip", type(e).__name__)
        return None
