"""B4+: POST /v1/audio/render {kind: "buddy_alert", text} -> {audio_url}.
ElevenLabs renders `text` in ELEVENLABS_VOICE_ALERT once per sha256 of
(kind, voice, text) into cloud/audio_cache/ (gitignored and a compose volume:
clips can contain names); a cache hit makes no API call. The clip is served
at GET /v1/audio/{hash}.mp3 (public; the 64-hex hash is unguessable).
VOICE_BACKEND=none renders nothing (audio_url null). The text never carries a
glucose value (invariant 15): anything that looks like one is refused before
any render (main.py answers 422).

The same approach as backend/app/voice_out.py (render once, write through a
.part file, never re-render), but over plain httpx so tests inject a
transport and never reach the network."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path

import httpx

log = logging.getLogger("irin.cloud.audio")

VOICE_BACKEND = os.environ.get("VOICE_BACKEND", "none")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ALERT = os.environ.get("ELEVENLABS_VOICE_ALERT", "")
DOMAIN = os.environ.get("DOMAIN", "irin-out-of-sleep-at-hackgt.tech")
CACHE_DIR = Path(__file__).resolve().parent / "audio_cache"
ELEVENLABS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice}"
MODEL_ID = "eleven_multilingual_v2"
TRANSPORT: httpx.AsyncBaseTransport | None = None  # tests inject a MockTransport
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
# a number with a unit, or any glucose word: refused (a buddy clip names a person and minutes only)
_GLUCOSE_RE = re.compile(r"\d\s*mg|\b(glucose|mg|mgdl|dl)\b", re.IGNORECASE)


class RenderError(RuntimeError):
    pass


def looks_like_glucose(text: str) -> bool:
    return bool(_GLUCOSE_RE.search(text))


def clip_hash(kind: str, voice: str, text: str) -> str:
    return hashlib.sha256(json.dumps([kind, voice, text]).encode("utf-8")).hexdigest()


def clip_path(h: str) -> Path:
    return CACHE_DIR / f"{h}.mp3"


def public_url(h: str) -> str:
    base = "http://localhost:8200" if DOMAIN in ("", "localhost") else f"https://cloud.{DOMAIN}"
    return f"{base}/v1/audio/{h}.mp3"


async def render(kind: str, text: str) -> str | None:
    """The public URL of the cached clip, rendering it on a miss; None when the
    voice backend is off or unconfigured. RenderError when ElevenLabs fails."""
    if VOICE_BACKEND != "elevenlabs":
        return None
    voice = ELEVENLABS_VOICE_ALERT
    if not ELEVENLABS_API_KEY or not voice:
        log.info("voice backend elevenlabs without a key or alert voice id; no clip")
        return None
    h = clip_hash(kind, voice, text)
    path = clip_path(h)
    if path.exists():
        return public_url(h)
    try:
        async with httpx.AsyncClient(timeout=20.0, transport=TRANSPORT) as client:
            r = await client.post(ELEVENLABS_URL.format(voice=voice), params={"output_format": "mp3_44100_64"},
                                  headers={"xi-api-key": ELEVENLABS_API_KEY, "accept": "audio/mpeg"},
                                  json={"text": text, "model_id": MODEL_ID})
        r.raise_for_status()
        if not r.content:
            raise RenderError("empty audio")
    except (httpx.HTTPError, RenderError) as e:
        log.warning("audio render failed (%s)", type(e).__name__)
        raise RenderError(type(e).__name__) from e
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.part")
    tmp.write_bytes(r.content)
    tmp.replace(path)  # atomic: a racing render of the same text only rewrites the same bytes
    log.info("audio rendered kind=%s hash=%s", kind, h[:12])
    return public_url(h)
