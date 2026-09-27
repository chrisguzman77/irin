"""Relay storage on MongoDB Atlas (R6): pymongo against ATLAS_URI, defaulting
to the compose file's mongo container. CIPHERTEXT AND PROFILE FIELDS ONLY;
nothing in any collection is ever a glucose value (invariant 20). Bearers are
stored as sha256 hashes (the plaintext is handed to the browser exactly once
after confirm). The emergency number (B-steps) is encrypted with RELAY_KEY
before storage and never returned. RELAY_DB_NAME lets tests use a throwaway
database on the same server."""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone

from pymongo import ASCENDING, MongoClient
from pymongo.errors import OperationFailure, PyMongoError

ATLAS_URI = os.environ.get("ATLAS_URI", "mongodb://localhost:27017")
DB_NAME = os.environ.get("RELAY_DB_NAME", "relay")

COLLECTIONS = (
    "users", "buddy_links", "matches", "hub_listings", "hub_claims", "hub_calls",
    "pairings", "cards", "messages", "resolutions", "audit",
)

_client: MongoClient | None = None


def client() -> MongoClient:
    global _client
    if _client is None:
        # tz_aware: every datetime read back is UTC-aware, so isoformat() carries +00:00
        _client = MongoClient(ATLAS_URI, serverSelectionTimeoutMS=1500, tz_aware=True)
    return _client


def db():
    return client()[DB_NAME]


def now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_indexes() -> None:
    """The lookups the routes use. The hub has NO TTL on its expiry fields: an
    expired lease reopens the listing and an expired treating window returns it
    at TOP urgency (hub.py sweeps lazily), so a janitor deleting the document
    would lose the listing. Older deployments' TTL indexes are dropped."""
    listings = db()["hub_listings"]
    for old in ("ttl_claim", "ttl_treating"):
        try:
            listings.drop_index(old)
        except OperationFailure:
            pass
    listings.create_index([("listing_id", ASCENDING)], unique=True, name="listing_id")
    db()["hub_claims"].create_index([("claim_id", ASCENDING)], unique=True, name="claim_id")
    db()["hub_claims"].create_index([("closed", ASCENDING), ("expires_at", ASCENDING)], name="live")
    db()["hub_calls"].create_index([("source_key_hash", ASCENDING), ("delivered", ASCENDING)], name="device_poll")
    db()["pairings"].create_index([("token", ASCENDING)], unique=True, name="token")
    db()["pairings"].create_index([("doctor_id", ASCENDING)], name="doctor")
    db()["pairings"].create_index([("bearer_hash", ASCENDING)], name="bearer")
    db()["cards"].create_index([("recipient_id", ASCENDING), ("created_at", ASCENDING)], name="inbox")
    db()["cards"].create_index([("card_id", ASCENDING)], name="card_id")
    db()["messages"].create_index([("device_id", ASCENDING), ("status", ASCENDING)], name="device_poll")
    db()["audit"].create_index([("at", ASCENDING)], expireAfterSeconds=30 * 24 * 3600, name="ttl_audit")


def status() -> str:
    """'ok' when the store answers a ping; otherwise the failure, honestly."""
    try:
        client().admin.command("ping")
        return "ok"
    except PyMongoError as e:
        return f"unreachable: {type(e).__name__}"


def bearer_hash(bearer: str) -> str:
    return hashlib.sha256(bearer.encode("utf-8")).hexdigest()


def audit(route: str, **fields) -> None:
    """The 'what Impiricus sees' log: ids, timestamps, sizes, kinds, ciphertext
    prefixes. Never a plaintext field (there is none to log)."""
    db()["audit"].insert_one({"at": now(), "route": route, **fields})


def public(doc: dict | None, *drop: str) -> dict | None:
    """A document without Mongo's _id and the named fields."""
    if doc is None:
        return None
    return {k: v for k, v in doc.items() if k != "_id" and k not in drop}
