"""A2/R5+: owner pairing, the phone app <-> this Pi (relay/README.md, "Owner
pairing (A2, R5+)", PINNED 2026-09-27; the relay's routes are called exactly
as pinned there).

mint(): a 6-digit code and a 43+ char url-safe token, registered with the
relay under this device's source key, expiring in 10 WALL minutes (the same
pattern as rounds/pairing.py's TOKEN_TTL: a human looks at the QR, so the
deadline is scaled by clock.speed). The relay keeps the plaintext token only
until the code is redeemed by the phone, then only sha256(token); this Pi
keeps only sha256(token) too (kv `owner_pairing`), so a poll can be checked
against it without ever holding the plaintext again.

on_owner(): the device poll's `owner` key is the relay's view of this
device's owner pairing. A poll reporting "paired" is only accepted when its
token_sha256 matches the token this Pi minted (a stray or forged report is
ignored, never applied). Every accepted change broadcasts pairing_state
{kind: "owner", state, username} (main.py wires on_state to the hub).

revoke(): tells the relay (X-Source-Key; it revokes this device's owner
pairing) and marks the local state revoked regardless of the relay's answer,
matching pairing.py's revoke-is-instant-locally behavior.

token_matches() is the tunnel gate's helper (app/auth.py): a request that
arrived through the tunnel (header Cf-Connecting-Ip present) must carry the
bearer whose sha256 matches the paired token."""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import threading
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable

import httpx

from . import store
from .clock import clock

log = logging.getLogger("irin.owner")

TOKEN_TTL = timedelta(minutes=10)  # WALL minutes: a human reads the code, so the deadline is scaled by clock.speed
KV_KEY = "owner_pairing"

_DEFAULT_STATE = {"token_sha256": None, "state": "none", "username": None, "paired_at": None}


class OwnerError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status, self.detail = status, detail


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _load() -> dict:
    raw = store.get_kv(KV_KEY)
    if not raw:
        return dict(_DEFAULT_STATE)
    try:
        d = json.loads(raw)
    except ValueError:
        return dict(_DEFAULT_STATE)
    return {**_DEFAULT_STATE, **d}


def token_matches(token: str) -> bool:
    """True when `token` sha256-matches the paired owner token in kv. Used by
    auth.py's tunnel gate; reads the store directly so auth.py never needs an
    OwnerPairingService instance (no circular import)."""
    if not token:
        return False
    d = _load()
    return d.get("state") == "paired" and d.get("token_sha256") == _sha256(token)


class RelayOwner:
    """The relay's owner-pairing routes, source-key authenticated, outbound
    only (relay/README.md "Owner pairing"). Every relay error is a 502: the
    Pi keeps working without the relay, same as RelayPairing."""

    def __init__(self, relay_url: str, source_key: str, transport: httpx.BaseTransport | None = None) -> None:
        self.relay_url, self.source_key, self.transport = relay_url.rstrip("/"), source_key, transport

    def _call(self, method: str, path: str, **kw) -> httpx.Response:
        try:
            with httpx.Client(base_url=self.relay_url, headers={"X-Source-Key": self.source_key}, timeout=10.0,
                              transport=self.transport) as c:
                return c.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise OwnerError(502, f"relay unreachable ({type(e).__name__})")

    def register(self, code: str, device_id: str, device_url: str, token: str, expires_at: str) -> None:
        r = self._call("POST", "/v0/device/pairings", json={"code": code, "device_id": device_id,
                                                             "device_url": device_url, "token": token,
                                                             "expires_at": expires_at})
        if r.status_code != 200:
            raise OwnerError(502, f"relay refused the owner code ({r.status_code})")

    def revoke(self) -> None:
        r = self._call("DELETE", "/v0/device/pair")
        if r.status_code not in (200, 401):
            raise OwnerError(502, f"relay did not revoke the owner pairing ({r.status_code})")


@dataclass
class OwnerPairingService:
    relay: RelayOwner
    device_id: str
    device_url: str
    app_origin: str
    on_state: Callable[[dict], None] | None = None  # pairing_state {kind: "owner", state, username}
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # --- state the app and kiosk read ---

    def state(self) -> dict[str, Any]:
        d = _load()
        return {"state": d["state"], "username": d["username"], "paired_at": d["paired_at"]}

    def _changed(self) -> None:
        if self.on_state is not None:
            try:
                self.on_state(self.state())
            except Exception:
                log.exception("owner pairing_state observer failed")

    # --- the handshake ---

    def mint(self) -> dict[str, Any]:
        """A new code replaces this device's earlier unused one (ONE owner pairing
        per device); the token is kept here only as sha256, never again in the clear."""
        with self._lock:
            code = f"{secrets.randbelow(1_000_000):06d}"
            token = secrets.token_urlsafe(32)
            ttl = TOKEN_TTL * clock.speed  # 10 wall minutes whatever the replay speed
            expires_at = clock.now() + ttl
            self.relay.register(code, self.device_id, self.device_url, token, expires_at.isoformat())
            store.set_kv(KV_KEY, json.dumps({"token_sha256": _sha256(token), "state": "pending",
                                             "username": None, "paired_at": None}))
            self._changed()
            qr_url = f"{self.app_origin.rstrip('/')}/#pair={code}"  # the code only, never the token
            return {"code": code, "qr_url": qr_url, "expires_at": expires_at.isoformat(),
                    "expires_in_s": int(TOKEN_TTL.total_seconds())}

    def on_owner(self, owner: dict) -> None:
        """The device poll's `owner` key: a "paired" report is only accepted when
        its token_sha256 matches the token this Pi minted (a mismatch is dropped,
        never applied); other states update local state as reported."""
        with self._lock:
            d = _load()
            reported = owner.get("state")
            if reported == "paired":
                if not owner.get("token_sha256") or owner.get("token_sha256") != d["token_sha256"]:
                    log.warning("owner poll reported 'paired' with a token this Pi did not mint; ignored")
                    return
                updated = {"token_sha256": d["token_sha256"], "state": "paired",
                          "username": owner.get("username"),
                          "paired_at": owner.get("paired_at") or clock.now().isoformat()}
            elif reported in ("none", "pending", "revoked"):
                updated = {**d, "state": reported}
            else:
                return
            if updated == d:
                return
            store.set_kv(KV_KEY, json.dumps(updated))
            self._changed()

    def revoke(self) -> dict[str, Any]:
        """Instant locally regardless of the relay's answer, like pairing.py's revoke."""
        with self._lock:
            try:
                self.relay.revoke()
            except OwnerError:
                log.warning("relay revoke failed; the device side is revoked regardless")
            d = _load()
            store.set_kv(KV_KEY, json.dumps({**d, "state": "revoked"}))
            self._changed()
            return self.state()
