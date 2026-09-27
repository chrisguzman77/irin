"""R5: doctor / buddy pairing. POST /api/pair/start (PIN) mints a 128-bit
single-use token with a 10-minute expiry on clock.py, registers {token,
device_pk, is_demo} with the relay, and returns the QR URL
<INBOX_URL>/pair#token=...&device_pk=...&relay=... (everything after # never
reaches a server). The browser generates the doctor keypair and posts
doctor_pk; the Pi polls the relay and both sides show code4; the patient
confirms on the device with a FRESH PIN -> Pairing paired, the relay issues
the doctor's bearer, pairing_state broadcast. Revoke from either side
deletes keys on both sides. peer_kind is doctor | buddy (the watcher page
is the buddy's browser). Demo pairings carry is_demo and only ever receive
demo cards (invariant 11). The owner pairing (DevicePairing) is R5+."""

from __future__ import annotations

import logging
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

import httpx

from .. import store
from ..clock import clock
from ..contracts import Pairing
from nacl.public import PublicKey

from .crypto import code4, unb64

log = logging.getLogger("irin.rounds.pairing")

TOKEN_TTL = timedelta(minutes=10)  # WALL minutes: a human scans the QR, so the deadline is scaled by clock.speed


class PairingError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status, self.detail = status, detail


class RelayPairing:
    """The relay's pairing routes (relay/README.md), source-key authenticated,
    outbound only. R6 implements them; tests inject a MockTransport."""

    def __init__(self, relay_url: str, source_key: str, transport: httpx.BaseTransport | None = None) -> None:
        self.relay_url, self.source_key, self.transport = relay_url.rstrip("/"), source_key, transport

    def _call(self, method: str, path: str, **kw) -> httpx.Response:
        """Every relay error is a 502 to the app: the Pi keeps working without the relay."""
        try:
            with httpx.Client(base_url=self.relay_url, headers={"X-Source-Key": self.source_key}, timeout=10.0,
                              transport=self.transport) as c:
                return c.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise PairingError(502, f"relay unreachable ({type(e).__name__})")

    def register(self, token: str, device_pk: str, is_demo: bool, peer_kind: str, device_id: str = "") -> None:
        r = self._call("POST", "/v0/pair", json={"token": token, "device_pk": device_pk, "is_demo": is_demo,
                                                  "peer_kind": peer_kind, "device_id": device_id})
        if r.status_code != 200:
            raise PairingError(502, f"relay refused the pairing token ({r.status_code})")

    def state(self, token: str) -> dict[str, Any]:
        """{status: pending | completed | expired, doctor_pk?, doctor_display_name?}"""
        r = self._call("GET", f"/v0/pair/{token}")
        if r.status_code == 404:
            return {"status": "expired"}
        if r.status_code != 200:
            raise PairingError(502, f"relay unavailable ({r.status_code})")
        return r.json()

    def confirm(self, token: str) -> dict[str, Any]:
        """-> {pairing_id, doctor_id}; the relay issues the doctor's bearer here."""
        r = self._call("POST", f"/v0/pair/{token}/confirm")
        if r.status_code != 200:
            raise PairingError(502, f"relay did not confirm ({r.status_code})")
        return r.json()

    def revoke(self, pairing_id: str) -> None:
        r = self._call("POST", f"/v0/pair/{pairing_id}/revoke")
        if r.status_code not in (200, 404):
            raise PairingError(502, f"relay did not revoke ({r.status_code})")


@dataclass
class PendingPairing:
    token: str
    peer_kind: str
    is_demo: bool
    started_at: datetime
    expires_at: datetime
    doctor_pk: str | None = None
    doctor_display_name: str | None = None
    used: bool = False

    def expired(self) -> bool:
        return clock.now() >= self.expires_at


@dataclass
class PairingService:
    relay: RelayPairing
    device_id: str
    device_pk_fn: Callable[[], str]  # crypto.device_public_key: generated on first use, never at import
    inbox_url: str
    watch_url: str
    relay_url: str
    is_demo: Callable[[], bool] = lambda: False
    on_state: Callable[[dict], None] | None = None  # pairing_state broadcast
    pending: PendingPairing | None = None
    pairings: dict[str, Pairing] = field(default_factory=dict)  # by doctor_id, loaded from the store
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        for p in store.select_pairings():
            self.pairings[p.doctor_id] = p

    def cancel(self) -> None:
        """A mode switch: the pending token is dropped (its clock and its is_demo no longer hold)."""
        with self._lock:
            if self.pending is not None:
                self.pending = None
                self._changed()

    @property
    def device_pk(self) -> str:
        return self.device_pk_fn()

    # --- state the app and kiosk read ---

    def state(self) -> dict[str, Any]:
        pend = self.pending
        if pend is not None and (pend.used or pend.expired()):
            pend = None
        return {
            "status": ("awaiting_confirm" if pend and pend.doctor_pk else "awaiting_scan" if pend else "idle"),
            "peer_kind": pend.peer_kind if pend else None,
            "expires_at": pend.expires_at.isoformat() if pend else None,
            "doctor_display_name": pend.doctor_display_name if pend else None,
            "code4": code4(self.device_pk, pend.doctor_pk, pend.token) if pend and pend.doctor_pk else None,
            "pairings": [p.model_dump(mode="json", exclude={"doctor_pk"}) for p in self.pairings.values()],
        }

    def _changed(self) -> None:
        if self.on_state is not None:
            try:
                self.on_state(self.state())
            except Exception:
                log.exception("pairing_state observer failed")

    # --- the handshake ---

    def start(self, peer_kind: str = "doctor") -> dict[str, Any]:
        with self._lock:
            token = secrets.token_hex(16)  # 128 bits, single use
            now = clock.now()
            demo = self.is_demo()
            self.relay.register(token, self.device_pk, demo, peer_kind, self.device_id)
            ttl = TOKEN_TTL * clock.speed  # 10 wall minutes whatever the replay speed
            self.pending = PendingPairing(token=token, peer_kind=peer_kind, is_demo=demo, started_at=now,
                                          expires_at=now + ttl)
            page = self.watch_url if peer_kind == "buddy" else self.inbox_url
            qr_url = f"{page.rstrip('/')}/pair#token={token}&device_pk={self.device_pk}&relay={self.relay_url}"
            self._changed()
            return {"token": token, "qr_url": qr_url, "expires_at": self.pending.expires_at.isoformat(),
                    "expires_in_s": int(TOKEN_TTL.total_seconds()), "peer_kind": peer_kind, "is_demo": demo}

    @staticmethod
    def _valid_pk(value: Any) -> bool:
        try:
            PublicKey(unb64(value))
            return isinstance(value, str)
        except Exception:
            return False

    def _poll_locked(self) -> None:
        pend = self.pending
        if pend is None or pend.used or pend.expired() or pend.doctor_pk is not None:
            return
        st = self.relay.state(pend.token)
        if st.get("status") == "completed" and st.get("doctor_pk") is not None:
            if not self._valid_pk(st.get("doctor_pk")):
                log.error("relay returned a malformed peer key; pairing dropped")
                self.pending = None
                self._changed()
                raise PairingError(502, "relay returned a malformed key; start again")
            name = st.get("doctor_display_name")
            pend.doctor_pk = st["doctor_pk"]
            pend.doctor_display_name = (str(name)[:60] if name else None) or pend.peer_kind.title()
            self._changed()
        elif st.get("status") == "expired":
            self.pending = None
            self._changed()

    def poll(self) -> dict[str, Any]:
        """Ask the relay whether the browser peer has posted its key; then state()."""
        with self._lock:
            self._poll_locked()
            return self.state()

    def confirm(self) -> Pairing:
        """The patient's fresh-PIN confirmation on the device. Single use: an
        expired or already-used token is refused and nothing is paired. The key
        sealed to is exactly the key code4 was shown for (poll stores it once)."""
        with self._lock:
            pend = self.pending
            if pend is None or pend.used:
                raise PairingError(409, "no pairing in progress")
            if pend.expired():
                self.pending = None
                self._changed()
                raise PairingError(410, "pairing token expired; start again")
            if pend.doctor_pk is None:
                self._poll_locked()
                if self.pending is not pend or pend.doctor_pk is None:
                    raise PairingError(409, "the other side has not scanned yet")
            result = self.relay.confirm(pend.token)
            doctor_id = result.get("doctor_id") or result.get("pairing_id")
            if not isinstance(doctor_id, str) or not doctor_id:
                raise PairingError(502, "relay confirmed without a doctor id")
            pend.used = True
            pairing = Pairing(device_id=self.device_id, doctor_id=doctor_id,
                              doctor_display_name=pend.doctor_display_name or pend.peer_kind.title(),
                              doctor_pk=pend.doctor_pk, status="paired", confirmed_at=clock.now(),
                              peer_kind=pend.peer_kind, is_demo=pend.is_demo)
            self.pairings[pairing.doctor_id] = pairing
            store.upsert_pairing(pairing)
            self.pending = None
            self._changed()
            return pairing

    def revoke(self, doctor_id: str) -> Pairing:
        """Instant: the relay drops the bearer and the peer key (by doctor_id), the device forgets the key."""
        with self._lock:
            pairing = self.pairings.get(doctor_id)
            if pairing is None:
                raise PairingError(404, "no such pairing")
            try:
                self.relay.revoke(doctor_id)
            except PairingError:
                log.warning("relay revoke failed; the device side is revoked regardless")
            pairing = pairing.model_copy(update={"status": "revoked", "doctor_pk": ""})
            self.pairings[doctor_id] = pairing
            store.upsert_pairing(pairing)
            self._changed()
            return pairing

    def apply_remote_states(self, states: list[dict]) -> None:
        """The relay's view of this device's pairings (the poll): a revoke from
        the inbox side takes effect here without calling the relay back."""
        for st in states:
            p = self.pairings.get(str(st.get("doctor_id")))
            if p is not None and st.get("status") == "revoked" and p.status != "revoked":
                self.pairings[p.doctor_id] = p.model_copy(update={"status": "revoked", "doctor_pk": ""})
                store.upsert_pairing(self.pairings[p.doctor_id])
                log.info("pairing %s revoked from the inbox", p.doctor_id)
                self._changed()

    # --- what the card path (R7) asks ---

    def recipients(self, is_demo: bool) -> list[Pairing]:
        """Paired peers a payload may be sealed to: never a revoked one, and a
        demo card only to a demo pairing (invariant 11)."""
        return [p for p in self.pairings.values() if p.status == "paired" and p.is_demo == is_demo and p.doctor_pk]
