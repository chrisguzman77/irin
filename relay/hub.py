"""B3: the hub. A patient's device opens a listing when the buddy rung fires
and the patient is hub-watchable; volunteers (a confirmed pairing with
peer_kind buddy) see it, one claims it under an exclusive lease, reads the
patient's own pre-written script while the lease is live, and asks the relay
to chime the patient's device. No glucose value, location, phone number, or
email ever reaches this module (invariant 15): the listing body forbids every
field it does not name. Every claim is audited (invariant 16). Demo listings
are seen and claimed only by demo volunteers (invariant 18). The hub is
ADDITIVE ONLY (invariant 13): nothing here gates a local alarm.

Expiry is lazy: every hub route first sweeps expired leases (the listing
reopens) and expired treating windows (the listing returns at TOP urgency),
reading time from store.now() so tests move the clock without sleeping.

The script: the device seals {"steps": [...]} with nacl secretbox under
HUB_SCRIPT_KEY (32 bytes, base64; shared by the device and the relay) and
posts script_ciphertext + script_nonce with the listing. The relay stores
only the sealed form and opens it only for the live claim-holder (demo tier;
the production answer, the device sealing to the claim-holder's key on
demand, is in docs/plans/chris.md B3). If HUB_SCRIPT_KEY is unset the relay
generates one per process, so only an in-process sealer (the tests) can post
a script that opens."""

from __future__ import annotations

import base64
import json
import os
import secrets
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from nacl.exceptions import CryptoError
from nacl.secret import SecretBox
from pydantic import BaseModel, ConfigDict, Field, model_validator

import store
from relay_api import B64, ID, _bearer_pairing, require_source_key

FORBIDDEN_LISTING_FIELDS = ("mgdl", "glucose", "location", "lat", "lon", "phone", "email", "number")
HUB_LEASE_S = int(os.environ.get("HUB_LEASE_S", "180"))
HUB_TREATING_S = int(os.environ.get("HUB_TREATING_S", "1200"))

if os.environ.get("HUB_SCRIPT_KEY"):
    SCRIPT_KEY = base64.b64decode(os.environ["HUB_SCRIPT_KEY"])
else:
    SCRIPT_KEY = secrets.token_bytes(SecretBox.KEY_SIZE)
    print("relay: HUB_SCRIPT_KEY unset; generated a per-process key (device-sealed scripts will not open)")
if len(SCRIPT_KEY) != SecretBox.KEY_SIZE:
    raise RuntimeError("HUB_SCRIPT_KEY must be 32 bytes, base64")

router = APIRouter(prefix="/v0/hub")


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


# ---------------------------------------------------------------- models


class ListingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    listing_id: str = Field(pattern=ID)
    first_name: str = Field(min_length=1, max_length=40, pattern=r"^[^\d@]{1,40}$")  # no digits, no @: no number or email smuggled in
    confidence: Literal["device_confirmed", "unconfirmed"]
    elapsed_min: int = Field(ge=0, le=1440)
    urgency: int = Field(ge=0, le=1000)
    is_demo: bool = False
    event_id: str = Field(pattern=ID)
    script_ciphertext: str | None = Field(default=None, pattern=B64, max_length=20_000)
    script_nonce: str | None = Field(default=None, pattern=B64, max_length=64)

    @model_validator(mode="before")
    @classmethod
    def _no_forbidden_fields(cls, data):
        if isinstance(data, dict):
            bad = [k for k in data if any(w in str(k).lower() for w in FORBIDDEN_LISTING_FIELDS)]
            if bad:
                raise ValueError(f"forbidden field(s) on a hub listing: {bad} (invariant 15)")
        return data


class ClaimIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    listing_id: str = Field(pattern=ID)


class CallIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim_id: str = Field(pattern=ID)


class TreatingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    listing_id: str = Field(pattern=ID)


class ResolveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    listing_id: str = Field(pattern=ID)
    outcome: str = Field(pattern=r"^[a-z_]{1,40}$")


# ---------------------------------------------------------------- helpers


def _listing_out(d: dict) -> dict:
    return {"listing_id": d["listing_id"], "first_name": d["first_name"], "status": d["status"],
            "confidence": d["confidence"], "elapsed_min": d["elapsed_min"], "urgency": d["urgency"],
            "claim_expires_at": _iso(d.get("claim_expires_at")), "treating_expires_at": _iso(d.get("treating_expires_at")),
            "is_demo": d["is_demo"]}


def _claim_out(d: dict) -> dict:
    return {"claim_id": d["claim_id"], "listing_id": d["listing_id"], "volunteer_id": d["volunteer_id"],
            "claimed_at": _iso(d["claimed_at"]), "expires_at": _iso(d["expires_at"]), "actions": d["actions"],
            "outcome": d.get("outcome")}


def _sweep() -> None:
    """Expired leases reopen their listing; expired treating windows return
    the listing to open at TOP urgency (above every open listing)."""
    now = store.now()
    listings, claims = store.db()["hub_listings"], store.db()["hub_claims"]
    for cl in claims.find({"closed": False, "expires_at": {"$lte": now}}):
        claims.update_one({"_id": cl["_id"], "closed": False}, {"$set": {"closed": True, "outcome": "lease_expired"}})
        listings.update_one({"listing_id": cl["listing_id"], "claim_id": cl["claim_id"], "status": "claimed"},
                            {"$set": {"status": "open", "claim_id": None, "claim_expires_at": None}})
        listings.update_one({"listing_id": cl["listing_id"], "claim_id": cl["claim_id"]},  # treating: the lease just ends
                            {"$set": {"claim_id": None, "claim_expires_at": None}})
        store.audit("hub.lease_expired", listing_id=cl["listing_id"], claim_id=cl["claim_id"])
    for li in listings.find({"status": "treating", "treating_expires_at": {"$lte": now}}):
        top = max([o["urgency"] for o in listings.find({"status": "open"}, {"urgency": 1})] + [li["urgency"]]) + 1
        listings.update_one({"_id": li["_id"], "status": "treating"},
                            {"$set": {"status": "open", "treating_expires_at": None, "urgency": top, "top": True}})
        store.audit("hub.treating_expired", listing_id=li["listing_id"], urgency=top)


def _volunteer(authorization: str | None = Header(default=None)) -> dict:
    """A confirmed buddy pairing's bearer (the hub is for verified accounts in standing, invariant 16)."""
    pairing = _bearer_pairing(authorization)
    if pairing.get("peer_kind") != "buddy":
        raise HTTPException(status_code=403, detail="the hub is for buddy pairings only")
    _sweep()
    return pairing


def _source(source_key: str = Depends(require_source_key)) -> str:
    _sweep()
    return source_key


def _owned_listing(listing_id: str, source_key: str) -> dict:
    d = store.db()["hub_listings"].find_one({"listing_id": listing_id})
    if d is None or d["source_key_hash"] != store.bearer_hash(source_key):
        raise HTTPException(status_code=404, detail="no such listing")
    return d


def _live_claim(claim_id: str, volunteer: dict) -> dict:
    """404 unknown, 403 not yours (or the other pool), 410 once the lease is over."""
    cl = store.db()["hub_claims"].find_one({"claim_id": claim_id})
    if cl is None:
        raise HTTPException(status_code=404, detail="no such claim")
    if cl["volunteer_id"] != volunteer["doctor_id"] or cl["is_demo"] != volunteer["is_demo"]:
        raise HTTPException(status_code=403, detail="not your claim")
    if cl["closed"] or cl["expires_at"] <= store.now():
        raise HTTPException(status_code=410, detail="the lease is over")
    return cl


# ---------------------------------------------------------------- device side (X-Source-Key)


@router.post("/listing")
async def post_listing(req: ListingIn, source_key: str = Depends(_source)) -> dict:
    """Open or refresh a listing (upsert by listing_id; the device re-posts
    with a new elapsed_min). Status is the relay's, never the body's."""
    if (req.script_ciphertext is None) != (req.script_nonce is None):
        raise HTTPException(status_code=422, detail="script_ciphertext and script_nonce travel together")
    if req.script_ciphertext is not None:
        try:
            script = json.loads(SecretBox(SCRIPT_KEY).decrypt(base64.b64decode(req.script_ciphertext),
                                                              base64.b64decode(req.script_nonce)))
        except (CryptoError, ValueError):
            raise HTTPException(status_code=422, detail="the script does not open under HUB_SCRIPT_KEY")
        if not (isinstance(script, dict) and isinstance(script.get("steps"), list)
                and all(isinstance(s, str) for s in script["steps"])):
            raise HTTPException(status_code=422, detail="the script must be {steps: [str]}")
    coll = store.db()["hub_listings"]
    key_hash = store.bearer_hash(source_key)
    existing = coll.find_one({"listing_id": req.listing_id})
    fields = {"first_name": req.first_name, "confidence": req.confidence, "elapsed_min": req.elapsed_min,
              "event_id": req.event_id, "updated_at": store.now()}
    if req.script_ciphertext is not None:
        fields |= {"script_ciphertext": req.script_ciphertext, "script_nonce": req.script_nonce}
    if existing is None:
        coll.insert_one({"listing_id": req.listing_id, "source_key_hash": key_hash, "is_demo": req.is_demo,
                         "status": "open", "urgency": req.urgency, "claim_id": None, "claim_expires_at": None,
                         "treating_expires_at": None, "created_at": store.now(), **fields})
    else:
        if existing["source_key_hash"] != key_hash:
            raise HTTPException(status_code=404, detail="no such listing")
        if existing["is_demo"] != req.is_demo:
            raise HTTPException(status_code=409, detail="is_demo cannot change on a listing")
        urgency = max(req.urgency, existing["urgency"]) if existing.get("top") else req.urgency  # TOP survives a re-post
        coll.update_one({"_id": existing["_id"]}, {"$set": {**fields, "urgency": urgency}})
    store.audit("hub.listing", listing_id=req.listing_id, is_demo=req.is_demo, confidence=req.confidence)
    return _listing_out(coll.find_one({"listing_id": req.listing_id}))


@router.post("/treating")
async def treating(req: TreatingIn, source_key: str = Depends(_source)) -> dict:
    """The patient tapped treating: the listing clears for HUB_TREATING_S; if
    no resolve arrives by then it returns to open at TOP urgency."""
    d = _owned_listing(req.listing_id, source_key)
    if d["status"] == "resolved":
        raise HTTPException(status_code=409, detail="listing already resolved")
    until = store.now() + timedelta(seconds=HUB_TREATING_S)
    store.db()["hub_listings"].update_one({"_id": d["_id"]}, {"$set": {"status": "treating", "treating_expires_at": until}})
    store.audit("hub.treating", listing_id=req.listing_id)
    return _listing_out(store.db()["hub_listings"].find_one({"_id": d["_id"]}))


@router.post("/resolve")
async def resolve(req: ResolveIn, source_key: str = Depends(_source)) -> dict:
    """Recovery or acknowledge on the device: the listing is resolved and a live claim closes with the outcome."""
    d = _owned_listing(req.listing_id, source_key)
    store.db()["hub_listings"].update_one({"_id": d["_id"]}, {"$set": {
        "status": "resolved", "outcome": req.outcome, "resolved_at": store.now(),
        "claim_id": None, "claim_expires_at": None, "treating_expires_at": None}})
    store.db()["hub_claims"].update_many({"listing_id": req.listing_id, "closed": False},
                                         {"$set": {"closed": True, "outcome": req.outcome}})
    store.audit("hub.resolve", listing_id=req.listing_id, outcome=req.outcome)
    return _listing_out(store.db()["hub_listings"].find_one({"_id": d["_id"]}))


@router.get("/audit")
async def audit(source_key: str = Depends(_source)) -> list[dict]:
    """Every claim on this device's listings, oldest first."""
    rows = store.db()["hub_claims"].find({"source_key_hash": store.bearer_hash(source_key)}).sort("claimed_at", 1)
    return [_claim_out(r) for r in rows]


# ---------------------------------------------------------------- volunteer side (bearer, peer_kind buddy)


@router.get("/list")
async def hub_list(volunteer: dict = Depends(_volunteer)) -> list[dict]:
    """Every unresolved listing in the volunteer's pool (demo or real),
    urgency first, then device_confirmed before unconfirmed."""
    rows = store.db()["hub_listings"].find({"status": {"$ne": "resolved"}, "is_demo": volunteer["is_demo"]})
    rows = sorted(rows, key=lambda d: (-d["urgency"], d["confidence"] != "device_confirmed", d["created_at"]))
    return [_listing_out(d) for d in rows]


@router.post("/claim")
async def claim(req: ClaimIn, volunteer: dict = Depends(_volunteer)):
    """An exclusive lease of HUB_LEASE_S; a second claim while it is live gets
    409 with the holder's expiry."""
    listings = store.db()["hub_listings"]
    d = listings.find_one({"listing_id": req.listing_id, "is_demo": volunteer["is_demo"]})
    if d is None:
        raise HTTPException(status_code=404, detail="no such listing")
    now = store.now()
    now = now.replace(microsecond=now.microsecond // 1000 * 1000)  # Mongo keeps milliseconds: the 200 and a later 409 agree
    claim_id = secrets.token_hex(8)
    expires = now + timedelta(seconds=HUB_LEASE_S)
    won = listings.find_one_and_update({"_id": d["_id"], "status": "open"},
                                       {"$set": {"status": "claimed", "claim_id": claim_id, "claim_expires_at": expires}})
    if won is None:
        cur = listings.find_one({"_id": d["_id"]})
        return JSONResponse(status_code=409, content={"detail": f"listing is {cur['status']}",
                                                      "holder_expires_at": _iso(cur.get("claim_expires_at"))})
    doc = {"claim_id": claim_id, "listing_id": req.listing_id, "volunteer_id": volunteer["doctor_id"], "claimed_at": now,
           "expires_at": expires, "actions": ["claim"], "outcome": None, "closed": False, "is_demo": d["is_demo"],
           "source_key_hash": d["source_key_hash"]}
    store.db()["hub_claims"].insert_one(doc)
    store.audit("hub.claim", listing_id=req.listing_id, claim_id=claim_id, volunteer_id=volunteer["doctor_id"],
                is_demo=d["is_demo"])
    return _claim_out(doc)


@router.get("/claim/{claim_id}/script")
async def script(claim_id: str, volunteer: dict = Depends(_volunteer)) -> dict:
    """The patient's own pre-written script: only the live claim-holder, only while the lease is live."""
    cl = _live_claim(claim_id, volunteer)
    d = store.db()["hub_listings"].find_one({"listing_id": cl["listing_id"]})
    if not d or not d.get("script_ciphertext"):
        raise HTTPException(status_code=404, detail="no script on this listing")
    steps = json.loads(SecretBox(SCRIPT_KEY).decrypt(base64.b64decode(d["script_ciphertext"]),
                                                     base64.b64decode(d["script_nonce"])))["steps"]
    store.db()["hub_claims"].update_one({"_id": cl["_id"]}, {"$push": {"actions": "script"}})
    store.audit("hub.script", listing_id=cl["listing_id"], claim_id=claim_id, volunteer_id=volunteer["doctor_id"])
    return {"steps": steps}


@router.post("/call")
async def call(req: CallIn, volunteer: dict = Depends(_volunteer)) -> dict:
    """Brokered: the patient's device chimes through its poll; no telephony, no numbers."""
    cl = _live_claim(req.claim_id, volunteer)
    store.db()["hub_calls"].insert_one({"listing_id": cl["listing_id"], "claim_id": cl["claim_id"], "at": store.now(),
                                        "source_key_hash": cl["source_key_hash"], "delivered": False})
    store.db()["hub_claims"].update_one({"_id": cl["_id"]}, {"$push": {"actions": "call"}})
    store.audit("hub.call", listing_id=cl["listing_id"], claim_id=cl["claim_id"], volunteer_id=volunteer["doctor_id"])
    return {"status": "ringing"}
