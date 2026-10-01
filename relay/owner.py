"""A2/R5+: owner pairing, the phone app <-> this user's Pi. PINNED contract in
relay/README.md ("Owner pairing (A2, R5+)"); build exactly that.

One `owner_pairings` document per device_id. device_id is bound to the source
key that first registers it (a later registration or DELETE from a different
key is 403/404), mirroring how relay_api.py ties a doctor pairing's
source_key_hash to the key that created it. A document can hold, at once, a
pending unredeemed registration (`code`, plaintext `token`, `expires_at`) and
the current active owner (`token_sha256`, `username`, `paired_at`), so the Pi
can mint a fresh code to re-pair without losing the currently paired phone
until the new code is actually redeemed: register only touches the top-level
`state` when there is no active owner yet ("a new registration replaces the
device's earlier unused code"); redeem always overwrites the owner fields
("a new redeem revokes the device's earlier paired token" -- the old token's
sha256 is simply gone, so it stops matching on DELETE or the Pi's poll
check). Audit rows carry device_id and outcome only, never code/token/username.
Phone accounts (Phase 1): `POST /v0/device/pair/check` lets Irin Cloud ask
whether a token is the device's ACTIVE owner token (X-Cloud-Key); `active_owner`
is the same question for the directory's link route."""

from __future__ import annotations

import time
from collections import defaultdict, deque

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

import store
from relay_api import ID, _eq, require_cloud_key, require_source_key

router = APIRouter(prefix="/v0/device")

BAD_CODE_DETAIL = "that code is not valid; show a new one on your Irin"
DEVICE_URL_PATTERN = r"^https?://[A-Za-z0-9.\-]+(:\d+)?(/.*)?$"


class OwnerRegister(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")
    device_id: str = Field(pattern=ID)
    device_url: str = Field(max_length=200, pattern=DEVICE_URL_PATTERN)
    token: str = Field(min_length=43, max_length=128, pattern=r"^[A-Za-z0-9_\-]+$")
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        return v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v  # a naive stamp is UTC


class OwnerCheck(BaseModel):
    device_id: str = Field(pattern=ID)
    token: str = Field(min_length=1, max_length=256)


class OwnerRedeem(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")
    username: str = Field(min_length=1, max_length=40, pattern=r"^[^\x00-\x1f]{1,40}$")


REDEEM_PER_IP_PER_MIN = 10  # a 6-digit code: a few honest typos, never a guessing run
REDEEM_GLOBAL_PER_MIN = 30  # so rotating IPs buys nothing (30 x 10 min = 0.03% of the code space)
_redeems: dict[str, deque] = defaultdict(deque)


def _redeem_limit(ip: str) -> None:
    t = time.monotonic()
    for key, cap in ((f"ip:{ip}", REDEEM_PER_IP_PER_MIN), ("all", REDEEM_GLOBAL_PER_MIN)):
        q = _redeems[key]
        while q and q[0] < t - 60:
            q.popleft()
        if len(q) >= cap:
            raise HTTPException(status_code=429, detail="too many tries; wait a minute")
    _redeems[f"ip:{ip}"].append(t)
    _redeems["all"].append(t)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/pairings")
async def register(req: OwnerRegister, source_key: str = Depends(require_source_key)) -> dict:
    """The Pi mints a code+token (10 wall minutes) and registers it. device_id
    must be this key's device: the first registration binds it, and no other
    key may register for it after."""
    key_hash = store.bearer_hash(source_key)
    coll = store.db()["owner_pairings"]
    existing = coll.find_one({"device_id": req.device_id})
    if existing is not None and existing.get("source_key_hash") != key_hash:
        raise HTTPException(status_code=403, detail="not your device")
    fields = {
        "device_id": req.device_id,
        "source_key_hash": key_hash,
        "device_url": req.device_url,
        "code": req.code,
        "token": req.token,
        "expires_at": req.expires_at,
    }
    if existing is None or existing.get("state") != "paired":
        fields["state"] = "pending"  # a paired device keeps reporting "paired" until this fresh code is redeemed
    coll.update_one({"device_id": req.device_id}, {"$set": fields}, upsert=True)
    store.audit("owner.register", device_id=req.device_id)
    return {"status": "registered"}


@router.post("/pair")
async def redeem(req: OwnerRedeem, request: Request) -> dict:
    """Public, single-use: the phone app redeems the 6-digit code shown on
    the kiosk. Unknown, expired, or already-used all get the same 404, and
    the relay then drops the plaintext token for good."""
    _redeem_limit(_client_ip(request))
    coll = store.db()["owner_pairings"]
    doc = coll.find_one({"code": req.code})
    if doc is None or "token" not in doc or store.now() > doc["expires_at"]:
        raise HTTPException(status_code=404, detail=BAD_CODE_DETAIL)
    token = doc["token"]
    had_previous_owner = "token_sha256" in doc
    coll.update_one(
        {"_id": doc["_id"]},
        {"$set": {"state": "paired", "token_sha256": store.bearer_hash(token), "username": req.username,
                  "paired_at": store.now()},
         "$unset": {"code": "", "token": "", "expires_at": "", "revoked_at": ""}},
    )
    store.audit("owner.redeem", device_id=doc["device_id"], replaced_previous=had_previous_owner)
    return {"device_id": doc["device_id"], "device_url": doc["device_url"], "token": token}


@router.delete("/pair")
async def unpair(request: Request, authorization: str | None = Header(default=None),
                  x_source_key: str | None = Header(default=None, alias="X-Source-Key")) -> dict:
    """From either side: the app's own owner bearer, or the Pi's source key
    (revokes that device's owner pairing). Idempotent."""
    coll = store.db()["owner_pairings"]
    if authorization:
        if not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="bearer required")
        token = authorization.split(" ", 1)[1].strip()
        doc = coll.find_one({"token_sha256": store.bearer_hash(token)})
        if doc is None:
            raise HTTPException(status_code=401, detail="unknown owner token")
        by = "app"
    else:
        source_key = require_source_key(request, x_source_key)
        doc = coll.find_one({"source_key_hash": store.bearer_hash(source_key)})
        if doc is None:
            raise HTTPException(status_code=404, detail="no owner pairing for this device")
        by = "device"
    coll.update_one({"_id": doc["_id"]}, {"$set": {"state": "revoked", "revoked_at": store.now()},
                                          "$unset": {"code": "", "token": "", "expires_at": ""}})  # a shown code dies too
    store.audit("owner.revoke", device_id=doc["device_id"], by=by)
    return {"status": "revoked"}


def active_owner(device_id: str, token: str) -> dict | None:
    """The device's owner pairing when `token` is its ACTIVE owner token: state paired and
    sha256 match. A pending (unredeemed) code's token and a revoked token never count."""
    doc = store.db()["owner_pairings"].find_one({"device_id": device_id})
    if doc is None or doc.get("state") != "paired" or not doc.get("token_sha256"):
        return None
    return doc if _eq(doc["token_sha256"], store.bearer_hash(token)) else None


@router.post("/pair/check")
async def check(req: OwnerCheck, _: None = Depends(require_cloud_key)) -> dict:
    """Irin Cloud's question (X-Cloud-Key): is this the device's active owner token?
    The token is compared by hash and never logged or stored."""
    doc = active_owner(req.device_id, req.token)
    store.audit("owner.check", device_id=req.device_id, ok=doc is not None)
    return {"ok": doc is not None, "username": doc.get("username") if doc is not None else None}


def poll(device_id: str, key_hash: str) -> dict:
    """Shaped for the device poll's `owner` key: the device's current owner
    pairing, never the plaintext token."""
    doc = store.db()["owner_pairings"].find_one({"device_id": device_id})
    if doc is None or doc.get("source_key_hash") != key_hash:
        return {"state": "none"}
    out: dict = {"state": doc.get("state", "none")}
    if doc.get("username") is not None:
        out["username"] = doc["username"]
    if doc.get("paired_at") is not None:
        out["paired_at"] = doc["paired_at"].isoformat()
    if doc.get("token_sha256") is not None:
        out["token_sha256"] = doc["token_sha256"]
    return out
