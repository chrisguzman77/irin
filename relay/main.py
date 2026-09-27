"""The Irin relay: a blind courier. It stores ciphertext and profile fields
only and can never read a card; it never holds a glucose value (invariant
20). Every route from "Relay API v0" (docs/plans/chris.md; relay/README.md is
authoritative) is present as a 501 stub naming the step that fills it in.

Environment: RELAY_SOURCE_KEYS (comma-separated device keys, checked as the
X-Source-Key header), RELAY_ADMIN_KEY (the demo-only Spark simulation),
RELAY_KEY (encrypts emergency numbers at rest), ATLAS_URI, NARRATIVE_BACKEND,
HUB_SCRIPT_KEY, HUB_LEASE_S, HUB_TREATING_S (hub.py), NARRATIVE_ROUTING, BACKBOARD_API_KEY, META_MODEL_API_KEY, ANTHROPIC_API_KEY
(the same four-link narrative chain as the Pi, for the buddy line, the match
explanation, and the emergency script), WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

import directory
import hub
import notify
import owner
import relay_api
import store

RELAY_SOURCE_KEYS = [k for k in os.environ.get("RELAY_SOURCE_KEYS", "").split(",") if k]
RELAY_ADMIN_KEY = os.environ.get("RELAY_ADMIN_KEY", "")
RELAY_KEY = os.environ.get("RELAY_KEY", "")
NARRATIVE_BACKEND = os.environ.get("NARRATIVE_BACKEND", "template")
NARRATIVE_ROUTING = os.environ.get("NARRATIVE_ROUTING", "")
BACKBOARD_API_KEY = os.environ.get("BACKBOARD_API_KEY", "")
META_MODEL_API_KEY = os.environ.get("META_MODEL_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
DOMAIN = os.environ.get("DOMAIN", "irin-out-of-sleep-at-hackgt.tech")


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        store.ensure_indexes()
    except Exception as e:  # no mongo on a laptop: boot anyway, health says so
        print(f"relay: store unavailable at boot ({type(e).__name__}); TTL indexes not created")
    yield


app = FastAPI(title="Irin relay API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[f"https://{DOMAIN}", f"https://doctor.{DOMAIN}", f"https://watch.{DOMAIN}",
                   f"https://family.{DOMAIN}", "http://localhost:5173", "http://localhost:8080", "http://localhost"]
                  + [o for o in os.environ.get("RELAY_EXTRA_ORIGINS", "").split(",") if o],  # the laptop fallback
    allow_methods=["*"],
    allow_headers=["*"],
)


def _stub(step: str):
    raise HTTPException(status_code=501, detail=f"not implemented: {step}")


@app.get("/v0/health")
async def health() -> dict:
    return {"ok": True, "store": store.status()}


app.include_router(relay_api.router)  # R6: pairing, cards, inbox, messages, device poll, resolutions, log; R13: resources
app.include_router(hub.router)  # B3: the hub
app.include_router(notify.router)  # B4+: the WhatsApp channel (buddy number, notify)
app.include_router(directory.router)  # B3+: the buddy directory and matching
app.include_router(owner.router)  # A2/R5+: owner pairing (phone app <-> this user's Pi)

# --- Spark, resources, log (R10, R13) ---
@app.post("/v0/spark/new_rx")
async def spark_new_rx(): _stub("R10 simulated Spark offer (demo-only, RELAY_ADMIN_KEY)")
