"""B4+: POST /v1/audio/render {kind, text, voice}: ElevenLabs with
ELEVENLABS_VOICE_ALERT (or the device voice), cached by text hash in
cloud/audio_cache/ (gitignored: clips can contain names), never re-rendering
the same text; returns audio_url on cloud.<domain>."""


def render(kind: str, text: str, voice: str) -> str:
    raise NotImplementedError("B4+: audio render")
