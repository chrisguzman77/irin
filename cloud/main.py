"""Irin Cloud (port 8200): the ingest endpoint the Pi forwards to (C1), the
dashboards API over Tiger Cloud (C3), the family rollup and bearers (C3),
and audio rendering (B4+). Every route is a 501 stub naming its step.
Nothing here is ever read by a card, a rule, or an alarm (invariant 21).

Environment: TIGER_URI, DEVICE_ID and DEVICE_TOKEN (ingest validates the one
hackathon device against cloud's OWN environment), VOICE_BACKEND,
ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ALERT (the buddy clip the relay links
to over WhatsApp), RELAY_KEY (audio/render from the relay), DOMAIN.
"""

from __future__ import annotations

import hmac
import os

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

TIGER_URI = os.environ.get("TIGER_URI", "postgresql://postgres:postgres@localhost:5432/irin")
DEVICE_ID = os.environ.get("DEVICE_ID", "")
DEVICE_TOKEN = os.environ.get("DEVICE_TOKEN", "")
VOICE_BACKEND = os.environ.get("VOICE_BACKEND", "none")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ALERT = os.environ.get("ELEVENLABS_VOICE_ALERT", "")
DOMAIN = os.environ.get("DOMAIN", "irin-out-of-sleep-at-hackgt.tech")

DASH_NAMES = ("nights", "tir", "profile", "lows_heatmap", "alarms", "near_misses", "basal",
              "sensor", "step_watch", "buddy", "under_the_hood")

app = FastAPI(title="Irin Cloud API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[f"https://{DOMAIN}", f"https://family.{DOMAIN}", f"https://watch.{DOMAIN}",
                   "http://localhost:5173", "http://localhost:8080"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def require_device_token(x_device_id: str | None = Header(default=None, alias="X-Device-Id"),
                         x_device_token: str | None = Header(default=None, alias="X-Device-Token")) -> str:
    if not DEVICE_ID or not DEVICE_TOKEN:
        raise HTTPException(status_code=503, detail="DEVICE_ID / DEVICE_TOKEN not configured")
    if x_device_id != DEVICE_ID or not x_device_token or not hmac.compare_digest(x_device_token, DEVICE_TOKEN):
        raise HTTPException(status_code=401, detail="bad device token")
    return x_device_id


def _stub(step: str):
    raise HTTPException(status_code=501, detail=f"not implemented: {step}")


@app.get("/v1/health")
async def health() -> dict:
    return {"ok": True, "voice": VOICE_BACKEND}


@app.post("/v1/ingest")
async def ingest(): _stub("C1 ingest (device token; upsert on (device_id, timestamp))")


@app.get("/v1/dash/{name}")
async def dash(name: str, days: int = 14):
    if name not in DASH_NAMES:
        raise HTTPException(status_code=404, detail=f"unknown dashboard; one of {DASH_NAMES}")
    _stub(f"C3 dash/{name}")


@app.get("/v1/family/last_night")
async def family_last_night(): _stub("C3 family rollup (family bearer)")
@app.post("/v1/family/bearers")
async def family_bearer_create(): _stub("C3 family bearers (PIN)")
@app.delete("/v1/family/bearers")
async def family_bearer_delete(): _stub("C3 family bearers (PIN)")
@app.post("/v1/audio/render")
async def audio_render(): _stub("B4+ audio render (ElevenLabs, cached by text hash)")
