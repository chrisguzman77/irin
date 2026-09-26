"""Relay storage on MongoDB Atlas (R6+): pymongo against ATLAS_URI, defaulting
to the compose file's mongo container. CIPHERTEXT AND PROFILE FIELDS ONLY;
nothing in any collection is ever a glucose value. The emergency number is
encrypted with RELAY_KEY before storage and never returned."""

from __future__ import annotations

import os

from pymongo import ASCENDING, MongoClient
from pymongo.errors import PyMongoError

ATLAS_URI = os.environ.get("ATLAS_URI", "mongodb://localhost:27017")
DB_NAME = "relay"

COLLECTIONS = (
    "users", "buddy_links", "matches", "hub_listings", "hub_claims",
    "pairings", "cards", "messages", "resolutions", "audit",
)

_client: MongoClient | None = None


def client() -> MongoClient:
    global _client
    if _client is None:
        _client = MongoClient(ATLAS_URI, serverSelectionTimeoutMS=1500)
    return _client


def db():
    return client()[DB_NAME]


def ensure_indexes() -> None:
    """The two TTL indexes on hub_listings, created at boot: a janitor only
    (the lease and treating expiry still run on clock.py so 60x replay works)."""
    listings = db()["hub_listings"]
    listings.create_index([("claim_expires_at", ASCENDING)], expireAfterSeconds=0, name="ttl_claim")
    listings.create_index([("treating_expires_at", ASCENDING)], expireAfterSeconds=0, name="ttl_treating")


def status() -> str:
    """'ok' when the store answers a ping; otherwise the failure, honestly."""
    try:
        client().admin.command("ping")
        return "ok"
    except PyMongoError as e:
        return f"unreachable: {type(e).__name__}"
