"""Phone-only accounts, Phase 1 (relay/README.md "Phone-only accounts", the
cloud part; mirrored in cloud/README.md): the one-time CGM feed check behind
POST /v1/accounts/verify, the relay calls (`/v0/users/{id}/verified` and
`/v0/device/pair/check`), the at-rest encryption of the feed URL and token
(nacl SecretBox under sha256(RELAY_KEY)), and the phone_accounts rows
(cloud/sql/007). The relay learns one boolean and never a URL, a token, or a
value (invariant 20); the glucose value the feed check reads is discarded
here and never stored, logged, or returned.

Outbound HTTP goes over plain httpx so tests inject a MockTransport
(TRANSPORT), never a live Nightscout or relay. Environment: RELAY_URL,
RELAY_CLOUD_KEY, RELAY_KEY.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import time

import httpx
import psycopg
from nacl.secret import SecretBox

log = logging.getLogger("irin.cloud.accounts")

RELAY_URL = os.environ.get("RELAY_URL", "").rstrip("/")
RELAY_CLOUD_KEY = os.environ.get("RELAY_CLOUD_KEY", "")
RELAY_KEY = os.environ.get("RELAY_KEY", "")
TRANSPORT: httpx.BaseTransport | None = None  # tests inject a MockTransport

FEED_TIMEOUT_S = 10.0
FEED_FRESH_MS = 15 * 60 * 1000
RELAY_TIMEOUT_S = 10.0
PAIR_CACHE_S = 300.0  # a pair/check answer is good for 5 minutes, both ways


class FeedError(Exception):
    """The feed is not live; .detail is one of the three pinned strings, never a value."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class NoSuchAccount(Exception):
    """The relay refused the user_id / bearer pair (401 or 404)."""


class RelayUnavailable(Exception):
    """The relay is unreachable or answered 5xx (or anything else unexpected)."""


def configured() -> bool:
    return bool(RELAY_KEY) and bool(RELAY_CLOUD_KEY)


# ---------------------------------------------------------------- at-rest encryption

def _box() -> SecretBox:
    return SecretBox(hashlib.sha256(RELAY_KEY.encode("utf-8")).digest())


def encrypt(text: str) -> bytes:
    return bytes(_box().encrypt(text.encode("utf-8")))


def decrypt(blob: bytes) -> str:
    return _box().decrypt(bytes(blob)).decode("utf-8")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- step 1: the feed

def check_feed(nightscout_url: str, token: str) -> None:
    """GET {url}/api/v1/entries.json?count=1&token=... (the same auth the Pi's
    datasource uses). Live = 2xx JSON list whose first entry has an `sgv` and a
    `date` (ms) within 15 minutes of now. Raises FeedError otherwise. The value
    read is discarded here."""
    url = f"{nightscout_url.rstrip('/')}/api/v1/entries.json"
    try:
        with httpx.Client(timeout=FEED_TIMEOUT_S, transport=TRANSPORT) as client:
            r = client.get(url, params={"count": "1", "token": token})
    except httpx.HTTPError as e:
        log.info("feed check: unreachable (%s)", type(e).__name__)
        raise FeedError("feed unreachable")
    if r.status_code in (401, 403):
        raise FeedError("feed refused the token")
    if not 200 <= r.status_code < 300:
        log.info("feed check: HTTP %s", r.status_code)
        raise FeedError("feed unreachable")
    try:
        entries = r.json()
    except ValueError:
        raise FeedError("feed unreachable")
    if not isinstance(entries, list):
        raise FeedError("feed unreachable")
    if not entries or not isinstance(entries[0], dict):
        raise FeedError("no reading in the last 15 minutes")
    first = entries[0]
    date = first.get("date")
    if "sgv" not in first or not isinstance(date, (int, float)):
        raise FeedError("no reading in the last 15 minutes")
    if abs(time.time() * 1000 - date) > FEED_FRESH_MS:
        raise FeedError("no reading in the last 15 minutes")


# ---------------------------------------------------------------- step 2: the relay

def _relay_post(path: str, json: dict, headers: dict[str, str]) -> httpx.Response:
    try:
        with httpx.Client(timeout=RELAY_TIMEOUT_S, transport=TRANSPORT) as client:
            return client.post(f"{RELAY_URL}{path}", json=json,
                               headers={"X-Cloud-Key": RELAY_CLOUD_KEY, **headers})
    except httpx.HTTPError as e:
        log.warning("relay %s: unreachable (%s)", path, type(e).__name__)
        raise RelayUnavailable()


def mark_verified(user_id: str, user_bearer: str) -> None:
    """POST /v0/users/{user_id}/verified with X-Cloud-Key and the user's own
    bearer, body {}. Relay 401/404 -> NoSuchAccount; 5xx or anything else ->
    RelayUnavailable."""
    r = _relay_post(f"/v0/users/{user_id}/verified", {}, {"Authorization": f"Bearer {user_bearer}"})
    if r.status_code in (401, 404):
        raise NoSuchAccount()
    if r.status_code != 200:
        log.warning("relay verified: HTTP %s", r.status_code)
        raise RelayUnavailable()


_pair_cache: dict[str, tuple[float, bool, str]] = {}  # sha256(token) -> (expires, ok, device_id asked)


def pair_check(device_id: str, token: str) -> bool:
    """POST /v0/device/pair/check {device_id, token} -> ok. The answer is cached
    5 minutes per sha256(token), ok or not; a cached ok only counts for the
    device it was given for. The token is never logged."""
    key, now = token_hash(token), time.monotonic()
    hit = _pair_cache.get(key)
    if hit and hit[0] > now:
        return hit[1] and hit[2] == device_id
    r = _relay_post("/v0/device/pair/check", {"device_id": device_id, "token": token}, {})
    if r.status_code != 200:
        log.warning("relay pair/check: HTTP %s", r.status_code)
        raise RelayUnavailable()
    try:
        ok = bool(r.json().get("ok"))
    except (ValueError, AttributeError):
        raise RelayUnavailable()
    _pair_cache[key] = (now + PAIR_CACHE_S, ok, device_id)
    return ok


# ---------------------------------------------------------------- step 3: the row

def upsert_account(uri: str, user_id: str, nightscout_url: str, nightscout_token: str) -> str:
    """Store the feed encrypted and a fresh dashboard token's hash (replacing the
    old one, so an earlier token stops working); returns the token, once."""
    token = secrets.token_urlsafe(32)
    with psycopg.connect(uri, connect_timeout=10) as conn:
        conn.execute(
            "INSERT INTO phone_accounts (user_id, nightscout_url_enc, nightscout_token_enc, verified_at, dash_token_hash)"
            " VALUES (%s, %s, %s, now(), %s)"
            " ON CONFLICT (user_id) DO UPDATE SET nightscout_url_enc = EXCLUDED.nightscout_url_enc,"
            " nightscout_token_enc = EXCLUDED.nightscout_token_enc, verified_at = now(),"
            " dash_token_hash = EXCLUDED.dash_token_hash",
            (user_id, encrypt(nightscout_url), encrypt(nightscout_token), token_hash(token)))
    return token


def user_for_dash_token(uri: str, token: str) -> str | None:
    with psycopg.connect(uri, connect_timeout=10) as conn:
        row = conn.execute("SELECT user_id FROM phone_accounts WHERE dash_token_hash = %s", (token_hash(token),)).fetchone()
    return row[0] if row else None
