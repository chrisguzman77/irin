"""B3+: the buddy directory and matching. POST /v0/users, GET
/v0/users/search, POST /v0/match, POST /v0/match/{id}/accept | decline,
POST /v0/match/{id}/pair_link, and the device poll's `matches` key. Plain
Python over the store: candidates are CGM-verified watchers (be_watcher on)
in the requester's pool (same is_demo); hours_covered (the requester's
22:00-08:00 sleep window in UTC intersected with the candidate's
availability, averaged per night), mirror (UTC offset difference 10-14 h),
shared_languages (casefold compare, stored as sent); score = hours_covered +
2 * mirror + len(shared_languages) + 3 when the candidate's home offset is
within 60 minutes of the requester's preferred buddy zone (timezones[1], v2);
sort, limit 3; declined candidates never return. Matching is mutual (v3): a
candidate is offered only when their preferred buddy zone (timezones[1]) is
within 60 minutes of the requester's home, and a seed selects everyone.
GET /v0/users/hub and its claim (v3) are the app's door into relay/hub.py's
listing store, lease, audit and sealed scripts. Seeded sample profiles
(seed: true, relay/seed_buddies.py) accept at once when a real user accepts,
never post a pair link, and every row naming one carries sample: true.
The introduction and why-this-match lines are narrative text (Muse) beside
the match; the scorer never reads them and an LLM never picks the buddy.

Phone-only accounts (Phase 1, pinned in relay/README.md): POST /v0/users/phone
signs a phone up with no Irin (a random bearer, stored only as
phone_bearer_hash; cgm_verified false until Irin Cloud marks it with
X-Cloud-Key AND the user's own bearer; a random source_key_hash exactly like a
seed's, so every query keyed on it keeps working); GET/PUT /v0/users/me; and
POST /v0/users/link joins the phone's document to a paired Irin's (owner.py
decides the device's active owner token). require_user accepts either hash.

Conventions the contract leaves open: a user's FIRST timezone is their home
zone (availability is local to it; offsets are read at store.now(), so DST
counts as of today); an availability row whose end is not after its start
runs past midnight into the next day; weekday numbering never matters to the
score because the requester's window is the same every night. Profiles and
matches hold no glucose value, location, phone number or email (invariant
15): the bodies forbid every field they do not name."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

import hub
import owner
import store
from relay_api import ID, _client_ip, _rate_limit, require_cloud_key, require_source_key

FORBIDDEN_FIELDS = ("mgdl", "mg_dl", "glucose", "location", "lat", "lon", "phone", "email", "number")
WEEK = 7 * 1440
NIGHT_START, NIGHT_LEN = 22 * 60, 10 * 60  # the requester's 22:00-08:00 local sleep window
TOP_N = 3
PHONE_SIGNUP_PER_MIN = 5  # on top of the usual per-IP bucket
NOT_PAIRED = "that phone is not paired with this Irin"
BOTH_HAVE_BUDDIES = "both profiles already have buddies; disconnect one first"

router = APIRouter(prefix="/v0")


class _Strict(BaseModel):
    """Any unnamed field is 422, and so is any field named like a glucose value or a contact detail."""
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _no_forbidden_fields(cls, data):
        if isinstance(data, dict):
            bad = [k for k in data if any(w in str(k).lower() for w in FORBIDDEN_FIELDS)]
            if bad:
                raise ValueError(f"forbidden field(s) in the directory: {bad} (invariant 15)")
        return data


class Slot(_Strict):
    weekday: int = Field(ge=0, le=6)
    start: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end: str = Field(pattern=r"^(([01]\d|2[0-3]):[0-5]\d|24:00)$")


class Optins(_Strict):
    have_buddy: bool = False
    be_watcher: bool = False
    hub_watchable: bool = False
    hub_volunteer: bool = False


class ProfileIn(_Strict):
    """The profile fields a phone sends (sign-up and PUT /v0/users/me): cgm_verified and is_demo are
    server-set on that path, so sending either is 422."""
    username: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.\-]{2,29}$")  # starts with a letter: never a phone number or an email
    first_name: str = Field(min_length=1, max_length=40, pattern=r"^[^\d@]{1,40}$")
    languages: list[str] = Field(default_factory=list, max_length=10)
    timezones: list[str] = Field(min_length=1, max_length=5)
    availability: list[Slot] = Field(default_factory=list, max_length=100)
    optins: Optins = Field(default_factory=Optins)

    @field_validator("username")
    @classmethod
    def _not_a_sample(cls, v: str) -> str:
        if v.lower().endswith("_sample"):
            raise ValueError("usernames ending in _sample are reserved for the sample profiles")
        return v

    @field_validator("languages")
    @classmethod
    def _languages(cls, v: list[str]) -> list[str]:
        if not all(re.fullmatch(r"[^\d@]{1,40}", x) for x in v):
            raise ValueError("a language is 1-40 characters with no digit and no @")
        return v

    @field_validator("timezones")
    @classmethod
    def _timezones(cls, v: list[str]) -> list[str]:
        for tz in v:
            try:
                ZoneInfo(tz)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError(f"not an IANA timezone: {tz!r}")
        return v


class UserIn(ProfileIn):
    """What the Pi sends (POST /v0/users): the profile plus its own word on cgm_verified and is_demo."""
    cgm_verified: bool = False
    is_demo: bool = False


class MatchIn(_Strict):
    """`{}`: the requester is the bearer."""


class VerifiedIn(_Strict):
    """`{}`: the cloud's verified mark names the user in the path and the bearer."""


class LinkIn(_Strict):
    device_id: str = Field(pattern=ID)
    owner_token: str = Field(min_length=1, max_length=256)


class PairLinkIn(_Strict):
    pair_url: str = Field(pattern=r"^https?://\S{1,2000}$")


# ---------------------------------------------------------------- auth


def _user_bearer(source_key: str, user_id: str) -> str:
    """Derived, so the same bearer comes back on every call and none is stored in the clear."""
    mac = hmac.new(source_key.encode(), f"irin-user-bearer:{user_id}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def require_user(request: Request, authorization: str | None = Header(default=None)) -> dict:
    _rate_limit(f"ip:{_client_ip(request)}")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="bearer required")
    h = store.bearer_hash(authorization.split(" ", 1)[1].strip())
    user = store.db()["users"].find_one({"$or": [{"bearer_hash": h}, {"phone_bearer_hash": h}]})  # device-derived or phone
    if user is None:
        raise HTTPException(status_code=401, detail="bad user bearer")
    _rate_limit(f"user:{user['user_id']}")
    return user


def _verified(user: dict) -> dict:
    if not user.get("cgm_verified"):
        raise HTTPException(status_code=403, detail="the directory is for CGM-verified accounts")
    return user


def _may_match(user: dict) -> dict:
    """The 403 rules of POST /v0/match (and the app hub's, v3)."""
    _verified(user)
    if not user["optins"].get("have_buddy"):
        raise HTTPException(status_code=403, detail="the have_buddy opt-in is off")
    return user


# ---------------------------------------------------------------- the scorer (deterministic)


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _offset_min(user: dict, at: datetime, index: int = 0) -> int:
    return int(at.astimezone(ZoneInfo(user["timezones"][index])).utcoffset().total_seconds() // 60)


def _wrap_diff(a: int, b: int) -> int:
    d = abs(a - b) % 1440
    return min(d, 1440 - d)


def _utc_intervals(local: list[tuple[int, int]], offset: int) -> list[tuple[int, int]]:
    """(start, length) in local minute-of-week -> [start, end) intervals in UTC minute-of-week, wraps split."""
    out = []
    for start, length in local:
        s = (start - offset) % WEEK
        if s + length <= WEEK:
            out.append((s, s + length))
        else:
            out += [(s, WEEK), (0, s + length - WEEK)]
    return out


def _merge(iv: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for s, e in sorted(iv):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def _availability(user: dict) -> list[tuple[int, int]]:
    rows = []
    for a in user.get("availability", []):
        s, e = _minutes(a["start"]), _minutes(a["end"])
        rows.append((a["weekday"] * 1440 + s, (e - s) % 1440 or 1440))  # end <= start runs past midnight; equal = all day
    return rows


def _selects(user: dict, other: dict, at: datetime) -> bool:
    """`user` picked `other`: user's preferred buddy zone (timezones[1]) is within 60 minutes of other's home."""
    return len(user["timezones"]) > 1 and _wrap_diff(_offset_min(user, at, 1), _offset_min(other, at)) <= 60


def score(req: dict, cand: dict, at: datetime) -> dict:
    """The score parts from the requester's side: hours of the requester's
    nights the candidate is awake for (average per night), mirror, shared languages."""
    req_off, cand_off = _offset_min(req, at), _offset_min(cand, at)
    nights = _utc_intervals([(d * 1440 + NIGHT_START, NIGHT_LEN) for d in range(7)], req_off)
    awake = _merge(_utc_intervals(_availability(cand), cand_off))
    covered = sum(max(0, min(e1, e2) - max(s1, s2)) for s1, e1 in nights for s2, e2 in awake)
    hours = round(covered / 60 / 7, 1)
    mirror = 600 <= _wrap_diff(req_off, cand_off) <= 840
    prefers = _selects(req, cand, at)
    theirs = {x.casefold() for x in cand.get("languages", [])}
    shared = {}  # casefold -> the requester's first spelling, so "English" and "english" count once
    for x in req.get("languages", []):
        if x.casefold() in theirs:
            shared.setdefault(x.casefold(), x)
    return {"score": round(hours + 2 * mirror + len(shared) + 3 * prefers, 1), "hours_covered": hours,
            "mirror": mirror, "shared_languages": sorted(shared.values())}


# ---------------------------------------------------------------- routes


def _pair_key(a: str, b: str) -> str:
    return ":".join(sorted((a, b)))


def _other(m: dict, user_id: str) -> str:
    return next(u for u in m["users"] if u != user_id)


def _sample(other: dict) -> dict:
    """`sample: true` on any row whose user is a seeded sample profile; real rows are unchanged."""
    return {"sample": True} if other.get("seed") else {}


def _match_out(m: dict, me: dict, other: dict) -> dict:
    return {"match_id": m["match_id"], "candidate_id": other["user_id"], "first_name": other["first_name"],
            **score(me, other, store.now()), "status": m["status"], **_sample(other)}


@router.post("/users")
async def users_upsert(req: UserIn, source_key: str = Depends(require_source_key)) -> dict:
    """One user per source key (upsert); the bearer is derived, so it is the same on every call."""
    key_hash = store.bearer_hash(source_key)
    fields = {**req.model_dump(), "updated_at": store.now()}
    doc = store.db()["users"].find_one_and_update(
        {"source_key_hash": key_hash}, {"$set": fields,
                                        "$setOnInsert": {"user_id": f"u-{secrets.token_hex(6)}", "created_at": store.now()}},
        upsert=True, return_document=ReturnDocument.AFTER)
    bearer = _user_bearer(source_key, doc["user_id"])
    if doc.get("bearer_hash") != store.bearer_hash(bearer):
        store.db()["users"].update_one({"_id": doc["_id"]}, {"$set": {"bearer_hash": store.bearer_hash(bearer)}})
    store.audit("directory.user", user_id=doc["user_id"], is_demo=req.is_demo, cgm_verified=req.cgm_verified)
    return {"user_id": doc["user_id"], "user_bearer": bearer}


@router.post("/users/phone")
async def users_phone(req: ProfileIn, request: Request) -> dict:
    """A phone with no Irin signs up (public, 5/min/IP on top of the usual bucket): a random bearer,
    returned once and stored only as phone_bearer_hash; cgm_verified false until the cloud's mark;
    a random source_key_hash like a seed's. No password and no recovery in Phase 1."""
    ip = _client_ip(request)
    _rate_limit(f"ip:{ip}")
    _rate_limit(f"phone-signup:{ip}", cap=PHONE_SIGNUP_PER_MIN)
    bearer, now = secrets.token_urlsafe(32), store.now()
    doc = {**req.model_dump(), "user_id": f"u-{secrets.token_hex(6)}", "phone": True, "cgm_verified": False,
           "is_demo": False, "phone_bearer_hash": store.bearer_hash(bearer),
           "source_key_hash": store.bearer_hash(secrets.token_hex(32)), "created_at": now, "updated_at": now}
    store.db()["users"].insert_one(doc)
    store.audit("directory.phone", user_id=doc["user_id"])
    return {"user_id": doc["user_id"], "user_bearer": bearer}


def _me_out(user: dict) -> dict:
    """The GET /v0/users/me shape; device_linked = an owner_pairings row names this document's source_key_hash."""
    linked = store.db()["owner_pairings"].find_one({"source_key_hash": user["source_key_hash"]}, {"_id": 1}) is not None
    return {"user_id": user["user_id"], "username": user["username"], "first_name": user["first_name"],
            "languages": user.get("languages", []), "timezones": user["timezones"],
            "availability": user.get("availability", []), "optins": user["optins"],
            "cgm_verified": bool(user.get("cgm_verified")), "is_demo": bool(user.get("is_demo")),
            "phone": bool(user.get("phone")), "device_linked": linked}


@router.get("/users/me")
async def users_me(user: dict = Depends(require_user)) -> dict:
    return _me_out(user)


@router.put("/users/me")
async def users_me_put(req: ProfileIn, user: dict = Depends(require_user)) -> dict:
    """The profile fields only: never cgm_verified, is_demo, user_id, either bearer hash, or source_key_hash."""
    doc = store.db()["users"].find_one_and_update({"_id": user["_id"]}, {"$set": {**req.model_dump(), "updated_at": store.now()}},
                                                  return_document=ReturnDocument.AFTER)
    store.audit("directory.me", user_id=user["user_id"])
    return _me_out(doc)


@router.post("/users/{user_id}/verified")
async def users_verified(user_id: str, body: VerifiedIn | None = None, _: None = Depends(require_cloud_key),
                         user: dict = Depends(require_user)) -> dict:
    """Irin Cloud verified the feed: BOTH X-Cloud-Key and the user's own bearer. The relay learns one
    boolean; the feed URL, token and every value stay in the cloud (invariant 20)."""
    if user["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="no such account")
    now = store.now()
    store.db()["users"].update_one({"_id": user["_id"]}, {"$set": {"cgm_verified": True, "verified_at": now}})
    store.audit("directory.verified", user_id=user_id)
    return {"user_id": user_id, "cgm_verified": True, "verified_at": now.isoformat()}


@router.post("/users/link")
async def users_link(req: LinkIn, user: dict = Depends(require_user)) -> dict:
    """A phone account joins its paired Irin: the owner token must be that device's active one, and the
    device's identity is its source_key_hash from owner_pairings. Cases (a)-(d) as pinned in the README."""
    if not user.get("phone"):
        raise HTTPException(status_code=403, detail="only a phone account links to an Irin")
    pairing = owner.active_owner(req.device_id, req.owner_token)
    if pairing is None:
        raise HTTPException(status_code=404, detail=NOT_PAIRED)
    key_hash, users, matches = pairing["source_key_hash"], store.db()["users"], store.db()["matches"]
    dev = users.find_one({"source_key_hash": key_hash})
    dev_id = dev["user_id"] if dev is not None else None
    if dev_id == user["user_id"]:  # already linked to this very Irin: nothing to move
        store.audit("directory.link", user_id=user["user_id"], device_user_id=dev_id, case="already")
        return {"user_id": user["user_id"], "device_linked": True}
    has_matches = lambda uid: matches.count_documents({"users": uid}, limit=1) > 0  # noqa: E731
    if dev is None:
        case = "a"
    elif not has_matches(dev_id):
        case = "b"  # the device's document had no buddies: it goes, and (a) applies
        users.delete_one({"_id": dev["_id"]})
    elif not has_matches(user["user_id"]):
        case = "c"  # the phone adopts the device's document (its buddies live there)
    else:
        raise HTTPException(status_code=409, detail=BOTH_HAVE_BUDDIES)
    if case == "c":
        users.update_one({"_id": dev["_id"]}, {"$set": {"phone": True, "phone_bearer_hash": user["phone_bearer_hash"]}})
        users.delete_one({"_id": user["_id"]})
        out_id = dev_id
    else:
        users.update_one({"_id": user["_id"]}, {"$set": {"source_key_hash": key_hash}})
        out_id = user["user_id"]
    store.audit("directory.link", user_id=user["user_id"], device_user_id=dev_id, case=case)
    return {"user_id": out_id, "device_linked": True}


@router.get("/users/search")
async def users_search(username: str = Query(min_length=1, max_length=30), user: dict = Depends(require_user)) -> list[dict]:
    """Username prefix, case-insensitive; CGM-verified users who opted in to buddying or watching, same pool only."""
    _verified(user)
    rows = store.db()["users"].find({
        "username": {"$regex": f"^{re.escape(username)}", "$options": "i"}, "cgm_verified": True,
        "is_demo": user["is_demo"], "user_id": {"$ne": user["user_id"]},
        "$or": [{"optins.have_buddy": True}, {"optins.be_watcher": True}]}).sort("username", 1).limit(20)
    out = [{"user_id": r["user_id"], "username": r["username"], "first_name": r["first_name"],
            "languages": r.get("languages", []), "be_watcher": r["optins"]["be_watcher"], **_sample(r)} for r in rows]
    store.audit("directory.search", user_id=user["user_id"], results=len(out))
    return out


@router.post("/match")
async def match(body: MatchIn | None = None, user: dict = Depends(require_user)) -> list[dict]:
    """Up to 3 offers, best first, among candidates who selected the requester
    (a seed selects everyone). Declined pairs never return; accepted pairs are already buddies."""
    _may_match(user)
    matches = store.db()["matches"]
    closed = {_other(m, user["user_id"]) for m in matches.find({"users": user["user_id"],
                                                                 "status": {"$in": ["declined", "accepted"]}})}
    now = store.now()
    cands = [c for c in store.db()["users"].find({"cgm_verified": True, "optins.be_watcher": True,
                                                   "is_demo": user["is_demo"], "user_id": {"$ne": user["user_id"]}})
             if c["user_id"] not in closed and (c.get("seed") or _selects(c, user, now))]
    ranked = sorted(((score(user, c, now), c) for c in cands), key=lambda p: (-p[0]["score"], p[1]["user_id"]))[:TOP_N]
    out = []
    for parts, c in ranked:
        key = _pair_key(user["user_id"], c["user_id"])
        try:
            m = matches.find_one_and_update(
                {"pair_key": key}, {"$setOnInsert": {"match_id": f"m-{secrets.token_hex(6)}", "pair_key": key,
                                                     "users": sorted([user["user_id"], c["user_id"]]), "accepted_by": [],
                                                     "status": "offered", "pair_urls": {}, "is_demo": user["is_demo"],
                                                     "created_at": now}},
                upsert=True, return_document=ReturnDocument.AFTER)
        except DuplicateKeyError:  # a concurrent offer of the same pair won the insert
            m = matches.find_one({"pair_key": key})
        if m["status"] != "offered":  # declined or accepted in the meantime
            continue
        out.append({"match_id": m["match_id"], "candidate_id": c["user_id"], "first_name": c["first_name"], **parts,
                    "status": "offered", **_sample(c)})
    store.audit("directory.match", user_id=user["user_id"], match_ids=[o["match_id"] for o in out])
    return out


def _my_match(match_id: str, user: dict) -> tuple[dict, dict]:
    m = store.db()["matches"].find_one({"match_id": match_id, "users": user["user_id"]})
    if m is None:
        raise HTTPException(status_code=404, detail="no such match")
    other = store.db()["users"].find_one({"user_id": _other(m, user["user_id"])})
    if other is None or other["is_demo"] != user["is_demo"]:
        raise HTTPException(status_code=409, detail="the other side is no longer in your pool")
    return m, other


@router.post("/match/{match_id}/accept")
async def match_accept(match_id: str, user: dict = Depends(require_user)) -> dict:
    m, other = _my_match(match_id, _verified(user))
    if m["status"] == "declined":
        raise HTTPException(status_code=409, detail="match declined")
    accepted_by = sorted(set(m["accepted_by"]) | {user["user_id"]} | ({other["user_id"]} if other.get("seed") else set()))
    status = "accepted" if set(accepted_by) == set(m["users"]) else "offered"
    m = store.db()["matches"].find_one_and_update({"_id": m["_id"], "status": {"$ne": "declined"}},
                                                  {"$set": {"accepted_by": accepted_by, "status": status}},
                                                  return_document=ReturnDocument.AFTER)
    if m is None:
        raise HTTPException(status_code=409, detail="match declined")
    store.audit("directory.accept", match_id=match_id, user_id=user["user_id"], status=status)
    return _match_out(m, user, other)


@router.post("/match/{match_id}/decline")
async def match_decline(match_id: str, user: dict = Depends(require_user)) -> dict:
    m, other = _my_match(match_id, user)
    if m["status"] == "accepted":
        raise HTTPException(status_code=409, detail="already accepted: revoke the pairing instead")
    m = store.db()["matches"].find_one_and_update({"_id": m["_id"], "status": {"$ne": "accepted"}},
                                                  {"$set": {"status": "declined", "declined_by": user["user_id"]}},
                                                  return_document=ReturnDocument.AFTER)
    if m is None:
        raise HTTPException(status_code=409, detail="already accepted: revoke the pairing instead")
    store.audit("directory.decline", match_id=match_id, user_id=user["user_id"])
    return _match_out(m, user, other)


@router.post("/match/{match_id}/pair_link")
async def pair_link(match_id: str, req: PairLinkIn, source_key: str = Depends(require_source_key)) -> dict:
    """The device's buddy pairing link, handed to the other side through its poll. Never logged."""
    user = store.db()["users"].find_one({"source_key_hash": store.bearer_hash(source_key)})
    if user is None:
        raise HTTPException(status_code=404, detail="no such match")
    m, _ = _my_match(match_id, user)
    if m["status"] != "accepted":
        raise HTTPException(status_code=409, detail="the match is not accepted by both sides")
    store.db()["matches"].update_one({"_id": m["_id"]}, {"$set": {f"pair_urls.{user['user_id']}": req.pair_url}})
    store.audit("directory.pair_link", match_id=match_id, user_id=user["user_id"])
    return {"stored": True}


# ---------------------------------------------------------------- the app's hub (v3; relay/hub.py's store)


def _hub_user(user: dict) -> dict:
    """Match's 403 rules, plus the hub_volunteer opt-in (invariant 16: volunteering is its own opt-in)."""
    _may_match(user)
    if not user["optins"].get("hub_volunteer"):
        raise HTTPException(status_code=403, detail="the hub_volunteer opt-in is off")
    hub._sweep()
    return user


def _visible_listings(user: dict, listing_id: str | None = None) -> list[tuple[dict, list[str]]]:
    """Unresolved listings in the user's pool plus the sample listings, never the user's own,
    whose person shares a language with the user (casefold) -> (listing, that person's languages)."""
    q = {"status": {"$ne": "resolved"}, "source_key_hash": {"$ne": user["source_key_hash"]},
         "$or": [{"is_demo": user["is_demo"], "sample": {"$ne": True}}, {"sample": True}]}
    if listing_id is not None:
        q["listing_id"] = listing_id
    rows = list(store.db()["hub_listings"].find(q))
    people = {u["source_key_hash"]: u.get("languages", []) for u in
              store.db()["users"].find({"source_key_hash": {"$in": [d["source_key_hash"] for d in rows]}})}
    mine = {x.casefold() for x in user.get("languages", [])}
    out = [(d, people[d["source_key_hash"]]) for d in rows
           if d["source_key_hash"] in people and mine & {x.casefold() for x in people[d["source_key_hash"]]}]
    return sorted(out, key=lambda p: (-p[0]["urgency"], p[0]["confidence"] != "device_confirmed", p[0]["created_at"]))


@router.get("/users/hub")
async def users_hub(user: dict = Depends(require_user)) -> list[dict]:
    """The app's hub list: never a glucose value, location, contact, or script (invariants 14, 15)."""
    rows = _visible_listings(_hub_user(user))
    store.audit("directory.hub", user_id=user["user_id"], results=len(rows))
    return [{"listing_id": d["listing_id"], "first_name": d["first_name"], "languages": langs,
             "elapsed_min": d["elapsed_min"], "urgency": d["urgency"], "confidence": d["confidence"],
             "sample": bool(d.get("sample"))} for d, langs in rows]


@router.post("/users/hub/{listing_id}/claim")
async def users_hub_claim(listing_id: str, user: dict = Depends(require_user)):
    """The watcher's exclusive lease, for an app user; the script travels ONLY in this response,
    while the claim is live (invariant 14). The live holder claiming again gets its own claim back."""
    rows = _visible_listings(_hub_user(user), listing_id)
    if not rows:
        raise HTTPException(status_code=404, detail="no such listing")
    d = rows[0][0]
    held = d.get("claim_id") and store.db()["hub_claims"].find_one(
        {"claim_id": d["claim_id"], "volunteer_id": user["user_id"], "closed": False, "expires_at": {"$gt": store.now()}})
    cl = held or hub.lease(d, user["user_id"])
    if isinstance(cl, JSONResponse):
        return cl
    store.db()["hub_claims"].update_one({"claim_id": cl["claim_id"]}, {"$push": {"actions": "script"}})
    store.audit("hub.script", listing_id=d["listing_id"], claim_id=cl["claim_id"], volunteer_id=user["user_id"])
    return {"claim_id": cl["claim_id"], "expires_at": hub._iso(cl["expires_at"]),
            "script": {"steps": hub.open_script(d) or []}}


def poll_matches(key_hash: str) -> list[dict]:
    """The device poll's `matches`: this device's user's matches, with the OTHER side's pairing link."""
    user = store.db()["users"].find_one({"source_key_hash": key_hash})
    if user is None:
        return []
    rows = list(store.db()["matches"].find({"users": user["user_id"]}).sort("created_at", 1).limit(50))
    others = {u["user_id"]: u for u in
              store.db()["users"].find({"user_id": {"$in": [_other(m, user["user_id"]) for m in rows]}})}
    out = []
    for m in rows:
        other = _other(m, user["user_id"])
        if other in others:
            out.append({"match_id": m["match_id"], "first_name": others[other]["first_name"], "status": m["status"],
                        "pair_url": m.get("pair_urls", {}).get(other), **_sample(others[other])})
    return out
