"""R6: the relay routes (pairing, cards, inbox, doctor messages, the device
poll, resolutions, the log). Auth: devices send X-Source-Key (one of
RELAY_SOURCE_KEYS); the inbox and the watcher page send the bearer issued at
pairing confirm; the pairing state and complete routes are public single-use
tokens. is_demo travels in the envelope and must match the pairing's, or the
card is rejected (invariant 11). The relay never holds a plaintext field of a
card or a message: it stores nonce + ciphertext and metadata (invariant 20)."""

from __future__ import annotations

import hmac
import os
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

import store

RELAY_SOURCE_KEYS = [k for k in os.environ.get("RELAY_SOURCE_KEYS", "").split(",") if k]
PAIR_TTL = timedelta(minutes=10)
BEARER_PICKUP_TTL = timedelta(minutes=10)  # the plaintext bearer waits this long for the browser, then is gone
RATE_LIMIT_PER_MIN = int(os.environ.get("RELAY_RATE_LIMIT_PER_MIN", "600"))
B64 = r"^[A-Za-z0-9+/]+={0,2}$"
ID = r"^[A-Za-z0-9_\-:.]{1,120}$"

router = APIRouter(prefix="/v0")
_hits: dict[str, deque] = defaultdict(deque)


def _rate_limit(key: str) -> None:
    q = _hits[key]
    t = time.monotonic()
    while q and q[0] < t - 60:
        q.popleft()
    if len(q) >= RATE_LIMIT_PER_MIN:
        raise HTTPException(status_code=429, detail="rate limit")
    q.append(t)


def _eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8", "replace"), b.encode("utf-8", "replace"))


def require_source_key(x_source_key: str | None = Header(default=None, alias="X-Source-Key")) -> str:
    """One source key = one device (the hackathon has one Pi); a key may only
    touch the pairings it registered."""
    _rate_limit("public")  # before auth too, so guessing is throttled
    keys = RELAY_SOURCE_KEYS or [k for k in os.environ.get("RELAY_SOURCE_KEYS", "").split(",") if k]
    if not x_source_key or not any(_eq(x_source_key, k) for k in keys):
        raise HTTPException(status_code=401, detail="bad source key")
    _rate_limit(f"src:{store.bearer_hash(x_source_key)[:16]}")
    return x_source_key


def _owned(doc: dict | None, source_key: str) -> dict:
    """The pairing must have been registered with this source key."""
    if doc is None or doc.get("source_key_hash") != store.bearer_hash(source_key):
        raise HTTPException(status_code=404, detail="no such pairing")
    return doc


def _bearer_pairing(authorization: str | None) -> dict:
    _rate_limit("public")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="bearer required")
    token = authorization.split(" ", 1)[1].strip()
    doc = store.db()["pairings"].find_one({"bearer_hash": store.bearer_hash(token), "status": "confirmed"})
    if doc is None:
        raise HTTPException(status_code=401, detail="bad or revoked bearer")
    _rate_limit(f"bearer:{doc['doctor_id']}")
    return doc


def require_bearer(authorization: str | None = Header(default=None)) -> dict:
    return _bearer_pairing(authorization)


# ---------------------------------------------------------------- pairing (R5/R6)


class PairStart(BaseModel):
    token: str = Field(pattern=r"^[0-9a-f]{32}$")
    device_pk: str = Field(pattern=B64, max_length=64)
    is_demo: bool = False
    peer_kind: Literal["doctor", "buddy"] = "doctor"
    device_id: str = Field(default="", max_length=64)


class PairComplete(BaseModel):
    doctor_pk: str = Field(pattern=B64, max_length=64)
    doctor_display_name: str = Field(min_length=1, max_length=60)


def _pairing_by_token(token: str) -> dict:
    doc = store.db()["pairings"].find_one({"token": token})
    if doc is None:
        raise HTTPException(status_code=404, detail="unknown token")
    if doc["status"] in ("pending", "completed") and store.now() > doc["expires_at"].replace(tzinfo=store.now().tzinfo):
        store.db()["pairings"].update_one({"_id": doc["_id"]}, {"$set": {"status": "expired"}})
        raise HTTPException(status_code=404, detail="token expired")
    if doc["status"] == "expired":
        raise HTTPException(status_code=404, detail="token expired")
    return doc


@router.post("/pair")
async def pair_start(req: PairStart, source_key: str = Depends(require_source_key)) -> dict:
    coll = store.db()["pairings"]
    if coll.find_one({"token": req.token}):
        raise HTTPException(status_code=409, detail="token already registered")
    now = store.now()
    coll.insert_one({"token": req.token, "device_pk": req.device_pk, "is_demo": req.is_demo, "peer_kind": req.peer_kind,
                     "device_id": req.device_id, "status": "pending", "created_at": now, "expires_at": now + PAIR_TTL,
                     "source_key_hash": store.bearer_hash(source_key)})
    store.audit("pair.start", token_prefix=req.token[:6], peer_kind=req.peer_kind, is_demo=req.is_demo)
    return {"status": "pending", "expires_at": (now + PAIR_TTL).isoformat()}


@router.get("/pair/{token}")
async def pair_state(token: str) -> dict:
    """Public, single-use token: what the browser and the Pi both poll. After
    confirm the bearer is handed to the browser exactly once."""
    _rate_limit("public")
    doc = _pairing_by_token(token)
    out = {"status": doc["status"], "is_demo": doc["is_demo"], "peer_kind": doc["peer_kind"],
           "device_id": doc.get("device_id", ""), "device_pk": doc["device_pk"], "doctor_pk": doc.get("doctor_pk"),
           "doctor_display_name": doc.get("doctor_display_name"), "doctor_id": doc.get("doctor_id")}
    if doc["status"] == "confirmed" and doc.get("bearer_pending"):
        stale = store.now() - doc["confirmed_at"].replace(tzinfo=store.now().tzinfo) > BEARER_PICKUP_TTL
        # atomic: exactly one reader ever receives the plaintext bearer
        claimed = store.db()["pairings"].find_one_and_update({"_id": doc["_id"], "bearer_pending": {"$exists": True}},
                                                             {"$unset": {"bearer_pending": ""}})
        if claimed is not None and not stale:
            out["bearer"] = claimed["bearer_pending"]
            store.audit("pair.bearer_delivered", doctor_id=doc["doctor_id"])
    return out


@router.post("/pair/{token}/complete")
async def pair_complete(token: str, req: PairComplete) -> dict:
    """The browser posts its public key: single use."""
    _rate_limit("public")
    doc = _pairing_by_token(token)
    if doc["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"token already {doc['status']}")
    r = store.db()["pairings"].update_one({"_id": doc["_id"], "status": "pending"},
                                          {"$set": {"status": "completed", "doctor_pk": req.doctor_pk,
                                                    "doctor_display_name": req.doctor_display_name, "completed_at": store.now()}})
    if r.matched_count == 0:
        raise HTTPException(status_code=409, detail="token already completed")
    store.audit("pair.complete", token_prefix=token[:6])
    return {"status": "completed"}


@router.post("/pair/{token}/confirm")
async def pair_confirm(token: str, source_key: str = Depends(require_source_key)) -> dict:
    """The device confirmed with a fresh PIN: the pairing is live and the bearer issued."""
    doc = _owned(_pairing_by_token(token), source_key)
    if doc["status"] != "completed":
        raise HTTPException(status_code=409, detail=f"token is {doc['status']}, not completed")
    doctor_id = doc.get("doctor_id") or f"{doc['peer_kind']}-{secrets.token_hex(6)}"
    bearer = secrets.token_urlsafe(32)
    r = store.db()["pairings"].update_one({"_id": doc["_id"], "status": "completed"},
                                          {"$set": {"status": "confirmed", "doctor_id": doctor_id, "confirmed_at": store.now(),
                                                    "bearer_hash": store.bearer_hash(bearer), "bearer_pending": bearer}})
    if r.matched_count == 0:
        raise HTTPException(status_code=409, detail="token already confirmed")
    store.audit("pair.confirm", doctor_id=doctor_id, is_demo=doc["is_demo"])
    return {"pairing_id": doctor_id, "doctor_id": doctor_id, "bearer_issued": True}


@router.post("/pair/{pairing_id}/revoke")
async def pair_revoke(pairing_id: str, x_source_key: str | None = Header(default=None, alias="X-Source-Key"),
                      authorization: str | None = Header(default=None)) -> dict:
    """From either side: the device (source key) or the inbox (its own bearer).
    Keys and the bearer are deleted; the doctor_id stays as a tombstone."""
    coll = store.db()["pairings"]
    if x_source_key:
        require_source_key(x_source_key)
        doc = _owned(coll.find_one({"doctor_id": pairing_id}) or coll.find_one({"token": pairing_id}), x_source_key)
    else:
        doc = _bearer_pairing(authorization)
        if doc["doctor_id"] != pairing_id:
            raise HTTPException(status_code=403, detail="not your pairing")
    if doc is None:
        raise HTTPException(status_code=404, detail="no such pairing")
    coll.update_one({"_id": doc["_id"]}, {"$set": {"status": "revoked", "revoked_at": store.now()},
                                          "$unset": {"doctor_pk": "", "bearer_hash": "", "bearer_pending": ""}})
    store.audit("pair.revoke", doctor_id=doc.get("doctor_id"), by="device" if x_source_key else "inbox")
    return {"status": "revoked"}


# ---------------------------------------------------------------- cards and the inbox (R6/R7)


class Envelope(BaseModel):
    recipient_id: str = Field(pattern=ID)
    sender_id: str = Field(pattern=ID)
    nonce: str = Field(pattern=B64, max_length=64)
    ciphertext: str = Field(pattern=B64, max_length=400_000)
    source: Literal["irin_bedside", "irin_brain"]
    kind: str = Field(pattern=r"^[a-z_]{1,40}$")
    program: Literal["standing", "step_watch"] | None = None
    is_demo: bool = False
    card_id: str | None = Field(default=None, pattern=ID)


def _confirmed_pairing(doctor_id: str) -> dict:
    doc = store.db()["pairings"].find_one({"doctor_id": doctor_id, "status": "confirmed"})
    if doc is None:
        raise HTTPException(status_code=404, detail="no confirmed pairing for that recipient")
    return doc


@router.post("/cards")
async def post_card(env: Envelope, source_key: str = Depends(require_source_key)) -> dict:
    """A sealed card for a paired recipient. is_demo must match the pairing's
    (a demo card never reaches a real doctor, a real card never a demo pairing)."""
    pairing = _owned(_confirmed_pairing(env.recipient_id), source_key)
    if pairing["is_demo"] != env.is_demo:
        raise HTTPException(status_code=409, detail="is_demo does not match the pairing")
    doc = {**env.model_dump(), "created_at": store.now()}
    coll = store.db()["cards"]
    if env.card_id:
        coll.update_one({"card_id": env.card_id, "recipient_id": env.recipient_id}, {"$set": doc}, upsert=True)
    else:
        coll.insert_one(doc)
    store.audit("card", sender=env.sender_id, recipient=env.recipient_id, kind=env.kind, program=env.program,
                is_demo=env.is_demo, size=len(env.ciphertext), ciphertext_prefix=env.ciphertext[:8])
    return {"stored": True, "card_id": env.card_id}


@router.get("/inbox/{recipient_id}")
async def inbox(recipient_id: str, since: str | None = Query(default=None), pairing: dict = Depends(require_bearer)) -> list[dict]:
    """The recipient's sealed cards, oldest first; the bearer must be theirs."""
    if pairing["doctor_id"] != recipient_id:
        raise HTTPException(status_code=403, detail="not your inbox")
    query: dict[str, Any] = {"recipient_id": recipient_id}
    if since:
        try:
            query["created_at"] = {"$gt": datetime.fromisoformat(since)}
        except ValueError:
            raise HTTPException(status_code=422, detail="since must be an ISO timestamp")
    rows = store.db()["cards"].find(query).sort("created_at", 1).limit(500)
    return [{**store.public(r), "created_at": r["created_at"].isoformat()} for r in rows]


# ---------------------------------------------------------------- doctor messages (R6/R9)


class MessageEnvelope(BaseModel):
    device_id: str = Field(default="", max_length=64)  # informational: the pairing decides the device
    message_id: str = Field(pattern=ID)
    nonce: str = Field(pattern=B64, max_length=64)
    ciphertext: str = Field(pattern=B64, max_length=100_000)
    kind: str = Field(pattern=r"^[a-z_]{1,40}$")
    is_demo: bool = False


class Resolution(BaseModel):
    status: Literal["confirmed", "declined", "expired"]


@router.post("/messages")
async def post_message(env: MessageEnvelope, pairing: dict = Depends(require_bearer)) -> dict:
    """Doctor -> device, sealed to the device key. The sender is the bearer's
    pairing and the DEVICE is that pairing's device: a doctor can never
    address a device they are not paired with."""
    if pairing["is_demo"] != env.is_demo:
        raise HTTPException(status_code=409, detail="is_demo does not match the pairing")
    device_id = pairing.get("device_id") or ""
    if env.device_id and env.device_id != device_id:
        raise HTTPException(status_code=403, detail="not paired with that device")
    coll = store.db()["messages"]
    if coll.find_one({"message_id": env.message_id}):
        raise HTTPException(status_code=409, detail="message_id already used")
    coll.insert_one({**env.model_dump(), "device_id": device_id, "sender_id": pairing["doctor_id"], "status": "pending",
                     "created_at": store.now()})
    store.audit("message", sender=pairing["doctor_id"], device=device_id, kind=env.kind, is_demo=env.is_demo,
                size=len(env.ciphertext), ciphertext_prefix=env.ciphertext[:8])
    return {"stored": True, "message_id": env.message_id}


@router.get("/device/{device_id}/messages")
async def device_messages(device_id: str, source_key: str = Depends(require_source_key)) -> dict:
    """The Pi's poll: pending sealed messages, and the state of its pairings
    (so a revoke from the inbox reaches the device)."""
    key_hash = store.bearer_hash(source_key)
    pairings = list(store.db()["pairings"].find({"device_id": device_id, "source_key_hash": key_hash,
                                                 "status": {"$in": ["confirmed", "revoked"]}}))
    senders = [p["doctor_id"] for p in pairings if p.get("doctor_id")]
    msgs = store.db()["messages"].find({"device_id": device_id, "status": "pending", "sender_id": {"$in": senders}}) \
        .sort("created_at", 1).limit(100)
    return {"messages": [{**store.public(m), "created_at": m["created_at"].isoformat()} for m in msgs],
            "pairings": [{"doctor_id": p["doctor_id"], "status": p["status"], "peer_kind": p["peer_kind"]}
                         for p in pairings if p.get("doctor_id")]}


@router.post("/messages/{message_id}/resolution")
async def resolution(message_id: str, res: Resolution, source_key: str = Depends(require_source_key)) -> dict:
    """Metadata only: confirmed | declined | expired, and when."""
    m = store.db()["messages"].find_one({"message_id": message_id})
    pairing = store.db()["pairings"].find_one({"doctor_id": m["sender_id"]}) if m else None
    if m is None or pairing is None or pairing.get("source_key_hash") != store.bearer_hash(source_key):
        raise HTTPException(status_code=404, detail="no such message")
    store.db()["messages"].update_one({"message_id": message_id}, {"$set": {"status": res.status, "resolved_at": store.now()}})
    store.audit("resolution", status=res.status)
    return {"message_id": message_id, "status": res.status}


@router.get("/messages/{message_id}")
async def message_state(message_id: str, pairing: dict = Depends(require_bearer)) -> dict:
    """The inbox reads the resolution word ('Patient confirmed 07:14')."""
    m = store.db()["messages"].find_one({"message_id": message_id, "sender_id": pairing["doctor_id"]})
    if m is None:
        raise HTTPException(status_code=404, detail="no such message")
    return {"message_id": message_id, "status": m["status"], "kind": m["kind"], "created_at": m["created_at"].isoformat(),
            "resolved_at": m["resolved_at"].isoformat() if m.get("resolved_at") else None}


# ---------------------------------------------------------------- the "what Impiricus sees" log


@router.get("/log")
async def log(limit: int = 100) -> list[dict]:
    """IDs, timestamps, sizes, kinds, ciphertext prefixes: never a plaintext field."""
    _rate_limit("public")
    rows = store.db()["audit"].find().sort("at", -1).limit(max(1, min(limit, 200)))
    return [{**store.public(r), "at": r["at"].isoformat()} for r in rows]
