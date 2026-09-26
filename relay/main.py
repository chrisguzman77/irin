"""The Irin relay: a blind courier. It stores ciphertext and profile fields
only and can never read a card; it never holds a glucose value (invariant
20). Every route from "Relay API v0" (docs/plans/chris.md; relay/README.md is
authoritative) is present as a 501 stub naming the step that fills it in.

Environment: RELAY_SOURCE_KEYS (comma-separated device keys, checked as the
X-Source-Key header), RELAY_ADMIN_KEY (the demo-only Spark simulation),
RELAY_KEY (encrypts emergency numbers at rest), ATLAS_URI, NARRATIVE_BACKEND,
NARRATIVE_ROUTING, BACKBOARD_API_KEY, META_MODEL_API_KEY, ANTHROPIC_API_KEY
(the same four-link narrative chain as the Pi, for the buddy line, the match
explanation, and the emergency script), WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

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


app.include_router(relay_api.router)  # R6: pairing, cards, inbox, messages, device poll, resolutions, log

# --- owner pairing (R5+) ---
@app.post("/v0/device/pairings")
async def device_pairings(): _stub("R5+ owner pairing register")
@app.post("/v0/device/pair")
async def device_pair(): _stub("R5+ owner pairing redeem")
@app.delete("/v0/device/pair")
async def device_unpair(): _stub("R5+ owner unpair")

# --- Spark, resources, log (R10, R13) ---
@app.post("/v0/spark/new_rx")
async def spark_new_rx(): _stub("R10 simulated Spark offer (demo-only, RELAY_ADMIN_KEY)")
@app.post("/v0/resources/request")
async def resources_request(): _stub("R13 resources request")

# --- hub (B3) ---
@app.post("/v0/hub/listing")
async def hub_listing(): _stub("B3 hub listing")
@app.get("/v0/hub/list")
async def hub_list(): _stub("B3 hub list")
@app.post("/v0/hub/claim")
async def hub_claim(): _stub("B3 hub claim")
@app.get("/v0/hub/claim/{claim_id}/script")
async def hub_script(claim_id: str): _stub("B3 hub script")
@app.post("/v0/hub/call")
async def hub_call(): _stub("B3 hub call")
@app.post("/v0/hub/treating")
async def hub_treating(): _stub("B3 hub treating")
@app.post("/v0/hub/resolve")
async def hub_resolve(): _stub("B3 hub resolve")
@app.get("/v0/hub/audit")
async def hub_audit(): _stub("B3 hub audit")

# --- buddy directory (B3+) ---
@app.post("/v0/users")
async def users_create(): _stub("B3+ directory users")
@app.get("/v0/users/search")
async def users_search(username: str = ""): _stub("B3+ directory search")
@app.post("/v0/match")
async def match(): _stub("B3+ directory match")
@app.post("/v0/match/{match_id}/accept")
async def match_accept(match_id: str): _stub("B3+ directory accept")
@app.post("/v0/match/{match_id}/decline")
async def match_decline(match_id: str): _stub("B3+ directory decline")
