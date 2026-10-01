"""Irin Cloud (port 8200): the ingest endpoint the Pi forwards to (C1), the
dashboards API over Tiger Cloud (C3), the family rollup and bearers (C3),
and audio rendering (B4+). Every route is a 501 stub naming its step.
Nothing here is ever read by a card, a rule, or an alarm (invariant 21).

Environment: TIGER_URI, DEVICE_ID and DEVICE_TOKEN (ingest validates the one
hackathon device against cloud's OWN environment), OWNER_BEARER (the owner's
dashboard token, sent as "Authorization: Bearer ..."), PIN (the same PIN as the
Pi's, for creating and revoking family bearers), DEVICE_TZ (the Pi's local time
zone, for the family page's stale flag; default America/New_York), VOICE_BACKEND,
ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ALERT (the buddy clip the relay links
to over WhatsApp), RELAY_KEY, DOMAIN. Audio render
(B4+) authenticates the device token, like ingest (cloud/audio.py).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time
import uuid
from typing import Literal

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

import audio
import dash as dash_mod
from ingest import IngestBatch, ingest as ingest_batch

log = logging.getLogger("irin.cloud")

TIGER_URI = os.environ.get("TIGER_URI", "postgresql://postgres:postgres@localhost:5432/irin")
DEVICE_ID = os.environ.get("DEVICE_ID", "")
DEVICE_TOKEN = os.environ.get("DEVICE_TOKEN", "")
OWNER_BEARER = os.environ.get("OWNER_BEARER", "")
PIN = os.environ.get("PIN", "")
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
    if x_device_id != DEVICE_ID or not x_device_token or not hmac.compare_digest(
            x_device_token.encode("utf-8", "replace"), DEVICE_TOKEN.encode("utf-8", "replace")):
        raise HTTPException(status_code=401, detail="bad device token")
    return x_device_id


def _same(given: str | None, secret: str) -> bool:
    return bool(given) and hmac.compare_digest(given.encode("utf-8", "replace"), secret.encode("utf-8", "replace"))


def _bearer(authorization: str | None) -> str | None:
    if authorization and authorization[:7].lower() == "bearer ":
        return authorization[7:].strip() or None
    return None


def require_owner(authorization: str | None = Header(default=None)) -> None:
    """The owner's dashboards: fail closed (503) until OWNER_BEARER is configured."""
    if not OWNER_BEARER:
        raise HTTPException(status_code=503, detail="OWNER_BEARER not configured")
    if not _same(_bearer(authorization), OWNER_BEARER):
        raise HTTPException(status_code=401, detail="bad owner bearer")


PIN_MAX_WRONG = 5
PIN_WINDOW_S = 600.0  # 5 wrong PINs within 10 minutes lock the client for 10 minutes
_pin_wrong: dict[str, list[float]] = {}
_pin_locked_until: dict[str, float] = {}


def _client_ip(request: Request) -> str:
    """X-Forwarded-For's first hop (Caddy), else the peer."""
    fwd = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return fwd or (request.client.host if request.client else "unknown")


def require_pin(request: Request, x_pin: str | None = Header(default=None, alias="X-PIN")) -> None:
    if not PIN:
        raise HTTPException(status_code=503, detail="PIN not configured")
    who, now = _client_ip(request), time.monotonic()
    if _pin_locked_until.get(who, 0.0) > now:
        raise HTTPException(status_code=429, detail="too many PIN attempts; wait 10 minutes")
    _pin_locked_until.pop(who, None)
    if _same(x_pin, PIN):
        _pin_wrong.pop(who, None)
        return
    recent = [t for t in _pin_wrong.get(who, []) if t > now - PIN_WINDOW_S] + [now]
    if len(recent) >= PIN_MAX_WRONG:
        _pin_wrong.pop(who, None)
        _pin_locked_until[who] = now + PIN_WINDOW_S
    else:
        _pin_wrong[who] = recent
    raise HTTPException(status_code=401, detail="bad PIN")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _stub(step: str):
    raise HTTPException(status_code=501, detail=f"not implemented: {step}")


@app.get("/v1/health")
async def health() -> dict:
    return {"ok": True, "voice": VOICE_BACKEND}


@app.post("/v1/ingest")
async def ingest(batch: IngestBatch, device_id: str = Depends(require_device_token)) -> dict:
    """C1: the Pi's 5-minute batch. The body's device_id must be the header's;
    demo rows are routed to <device_id>-demo inside ingest(). 503 when Tiger is
    unreachable so the Pi keeps its cursor and retries next tick."""
    if batch.device_id != device_id:
        raise HTTPException(status_code=400, detail="device_id does not match the token")
    try:
        return ingest_batch(batch)
    except psycopg.Error as e:
        log.warning("ingest failed: %s", type(e).__name__)
        raise HTTPException(status_code=503, detail="storage unavailable; retry")


@app.get("/v1/dash/{name}")
def dash(name: str, days: int = 14, demo: bool = False, _owner: None = Depends(require_owner)) -> dict:
    """C3: one chart's JSON (cloud/dash.py; shapes in cloud/README.md). demo=true
    reads <DEVICE_ID>-demo, so replayed data is never drawn as the real device."""
    if name not in DASH_NAMES:
        raise HTTPException(status_code=404, detail=f"unknown dashboard; one of {DASH_NAMES}")
    if not DEVICE_ID:
        raise HTTPException(status_code=503, detail="DEVICE_ID not configured")
    try:
        return dash_mod.query(name, days, f"{DEVICE_ID}-demo" if demo else DEVICE_ID, is_demo=demo)
    except psycopg.Error as e:
        log.warning("dash %s failed: %s", name, type(e).__name__)
        raise HTTPException(status_code=503, detail="storage unavailable; retry")


class BearerRequest(BaseModel):
    recipient_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")
    demo: bool = False


@app.get("/v1/family/last_night")
def family_last_night(authorization: str | None = Header(default=None)) -> dict:
    """C3: the family page's one read (fields pinned in cloud/README.md). A
    missing, unknown, or revoked family bearer gets 401."""
    token = _bearer(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="family bearer required")
    try:
        with psycopg.connect(dash_mod.TIGER_URI, connect_timeout=10) as conn:
            row = conn.execute("SELECT device_id FROM family_bearers WHERE token_hash = %s AND revoked_at IS NULL",
                               (_token_hash(token),)).fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="unknown or revoked family bearer")
        device = row[0]
        return dash_mod.family_last_night(device, is_demo=device.endswith("-demo"))
    except psycopg.Error as e:
        log.warning("family rollup failed: %s", type(e).__name__)
        raise HTTPException(status_code=503, detail="storage unavailable; retry")


@app.post("/v1/family/bearers")
def family_bearer_create(req: BearerRequest, _pin: None = Depends(require_pin)) -> dict:
    """C3: a new family bearer for one recipient; the token is returned ONCE
    (only its SHA-256 hash is stored)."""
    if not DEVICE_ID:
        raise HTTPException(status_code=503, detail="DEVICE_ID not configured")
    token = secrets.token_urlsafe(32)
    bearer_id = uuid.uuid4().hex[:16]
    device = f"{DEVICE_ID}-demo" if req.demo else DEVICE_ID
    try:
        with psycopg.connect(dash_mod.TIGER_URI, connect_timeout=10) as conn:
            created = conn.execute(
                "INSERT INTO family_bearers (bearer_id, recipient_id, device_id, token_hash)"
                " VALUES (%s, %s, %s, %s) RETURNING created_at",
                (bearer_id, req.recipient_id, device, _token_hash(token))).fetchone()[0]
    except psycopg.Error as e:
        log.warning("bearer create failed: %s", type(e).__name__)
        raise HTTPException(status_code=503, detail="storage unavailable; retry")
    return {"bearer_id": bearer_id, "recipient_id": req.recipient_id, "token": token,
            "is_demo": req.demo, "created_at": created}


@app.delete("/v1/family/bearers")
def family_bearer_delete(bearer_id: str, _pin: None = Depends(require_pin)) -> dict:
    """C3: revoke instantly; the token is refused from the next request on."""
    try:
        with psycopg.connect(dash_mod.TIGER_URI, connect_timeout=10) as conn:
            n = conn.execute("UPDATE family_bearers SET revoked_at = now() WHERE bearer_id = %s AND revoked_at IS NULL",
                             (bearer_id,)).rowcount
    except psycopg.Error as e:
        log.warning("bearer revoke failed: %s", type(e).__name__)
        raise HTTPException(status_code=503, detail="storage unavailable; retry")
    if n == 0:
        raise HTTPException(status_code=404, detail="no active bearer with that id")
    return {"bearer_id": bearer_id, "revoked": True}
class RenderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["buddy_alert"]
    text: str = Field(min_length=1, max_length=400)


@app.post("/v1/audio/render")
async def audio_render(req: RenderRequest, _device: str = Depends(require_device_token)) -> dict:
    """B4+: the buddy clip, rendered once per sha256(kind, voice, text) and
    cached (cloud/audio.py). {audio_url: null} while VOICE_BACKEND is not
    elevenlabs; 422 on anything that looks like a glucose value; 502 when
    ElevenLabs fails (the Pi sends the alert without a clip either way)."""
    if audio.looks_like_glucose(req.text):
        raise HTTPException(status_code=422, detail="the text looks like a glucose value (invariant 15)")
    try:
        return {"audio_url": await audio.render(req.kind, req.text)}
    except audio.RenderError:
        raise HTTPException(status_code=502, detail="render failed")


@app.get("/v1/audio/{name}")
async def audio_clip(name: str) -> FileResponse:
    """B4+: a rendered clip, public by its unguessable hash (the watcher page
    and WhatsApp fetch it without a credential)."""
    h = name.removesuffix(".mp3")
    if not name.endswith(".mp3") or not audio.HASH_RE.match(h) or not audio.clip_path(h).is_file():
        raise HTTPException(status_code=404, detail="no such clip")
    return FileResponse(audio.clip_path(h), media_type="audio/mpeg")
