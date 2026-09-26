"""R9: doctor messages and the patient's confirm. An incoming envelope from
the relay must open with the PAIRED doctor's key (crypto_box authenticates
the sender) and its is_demo must match the pairing's, else it is rejected
and logged. Opened messages are stored pending (24 h expiry on clock.py)
and broadcast as doctor_message_received. Nothing applies until the patient
confirms on the device with a FRESH PIN: insulin_change updates
Settings.basal_units (basal) and logs exactly one therapy_change Treatment
and marks the change date so Follow-up can start; plan_create / plan_update
/ proceed / hold_step / end_watch are handed to the Step Watch (R10) hook;
note / schedule_request / dismiss apply nothing. Decline and expiry apply
nothing; silence changes nothing. The resolution word (confirmed | declined
| expired) is posted to the relay, metadata only, and broadcast as
doctor_message_resolved (invariant 8)."""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from .. import store
from ..clock import clock
from ..contracts import DoctorMessage, Pairing, Settings, Treatment
from . import crypto

log = logging.getLogger("irin.rounds.messages")

EXPIRY = timedelta(hours=24)
THERAPY_CHANGE_KEY = "therapy_change_date"  # store.kv, per world: therapy_change_date:demo | :live
MAX_UNITS = 50.0  # the same bound as /api/log
HOLD_WEEKS = (2, 4, 8)


def therapy_change_key(is_demo: bool) -> str:
    return f"{THERAPY_CHANGE_KEY}:{'demo' if is_demo else 'live'}"


class MessageError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status, self.detail = status, detail


@dataclass
class DoctorMessages:
    settings: Settings
    pairings: Callable[[], dict[str, Pairing]]
    post_resolution: Callable[[str, str], Any]  # relay_client.post_resolution (async)
    on_received: Callable[[dict], None] | None = None
    on_resolved: Callable[[dict], None] | None = None
    on_plan_message: Callable[[DoctorMessage], None] | None = None  # R10 Step Watch
    is_demo: Callable[[], bool] = lambda: False

    # --- inbound ---

    def _open(self, env: dict[str, Any]) -> tuple[DoctorMessage, Pairing]:
        sender = str(env.get("sender_id", ""))
        pairing = self.pairings().get(sender)
        if pairing is None or pairing.status != "paired" or not pairing.doctor_pk:
            raise MessageError(403, "not from a paired doctor")
        if bool(env.get("is_demo", False)) != pairing.is_demo:
            raise MessageError(409, "is_demo does not match the pairing")
        try:
            plaintext = crypto.open_box(env["nonce"], env["ciphertext"], pairing.doctor_pk)
            msg = DoctorMessage.model_validate(json.loads(plaintext))
        except Exception as e:  # tampered, sealed by another key, or not a DoctorMessage
            raise MessageError(400, f"message did not open or validate ({type(e).__name__})")
        if msg.message_id != env.get("message_id") or msg.kind != env.get("kind"):
            raise MessageError(400, "envelope metadata does not match the sealed message")
        self._validate_kind(msg)  # a message the device could never apply is refused here, never shown
        return msg, pairing

    @staticmethod
    def _validate_kind(msg: DoctorMessage) -> None:
        if msg.kind == "insulin_change":
            u = msg.new_units
            if msg.insulin is None or u is None or not math.isfinite(u) or not (0 < u <= MAX_UNITS):
                raise MessageError(400, f"insulin_change needs insulin and new_units in (0, {MAX_UNITS:g}]")
        if msg.kind == "hold_step" and msg.hold_weeks not in HOLD_WEEKS:
            raise MessageError(400, f"hold_step needs hold_weeks in {HOLD_WEEKS}")
        if msg.kind in ("plan_create", "plan_update") and msg.plan is None:
            raise MessageError(400, f"{msg.kind} needs a plan")

    def receive(self, envelopes: list[dict[str, Any]]) -> list[DoctorMessage]:
        """The relay poll's messages. Known ids are skipped; the rest are opened,
        stored pending, and announced. A rejected envelope is logged and left
        pending on the relay (never resolved), so the doctor sees no receipt."""
        accepted = []
        for env in envelopes:
            mid = str(env.get("message_id", ""))
            if not mid or store.select_doctor_message(mid) is not None:
                continue
            if bool(env.get("is_demo", False)) != self.is_demo():
                continue  # the other world's message waits on the relay until the device is in that world
            try:
                msg, pairing = self._open(env)
            except MessageError as e:
                log.warning("doctor message %s rejected: %s", mid[:12], e.detail)
                continue
            now = clock.now()
            doc = {"message": msg.model_dump(mode="json"), "sender_id": pairing.doctor_id,
                   "doctor_display_name": pairing.doctor_display_name, "is_demo": pairing.is_demo,
                   "received_at": now.isoformat(), "expires_at": (now + EXPIRY).isoformat(), "resolution_posted": False}
            store.upsert_doctor_message(doc)
            accepted.append(msg)
            if self.on_received is not None:
                try:
                    self.on_received({**doc, "status": "pending"})
                except Exception:
                    log.exception("doctor_message_received observer failed")
        return accepted

    # --- what the screens read ---

    def pending(self) -> list[dict[str, Any]]:
        return [d for d in store.select_doctor_messages() if d["message"]["status"] == "pending"]

    def pending_messages(self) -> list[DoctorMessage]:
        return [DoctorMessage.model_validate(d["message"]) for d in self.pending()]

    # --- the patient's answer ---

    def _resolve(self, doc: dict[str, Any], status: str) -> dict[str, Any]:
        doc["message"]["status"] = status
        doc["resolved_at"] = clock.now().isoformat()
        store.upsert_doctor_message(doc)
        return doc

    async def _announce_resolution(self, doc: dict[str, Any]) -> None:
        mid, status = doc["message"]["message_id"], doc["message"]["status"]
        try:
            ok = bool(await self.post_resolution(mid, status))
        except Exception:
            log.exception("resolution not posted for %s", mid[:12])
            ok = False
        doc["resolution_posted"] = ok  # a relay outage never loses the receipt: the tick retries
        store.upsert_doctor_message(doc)
        if self.on_resolved is not None:
            try:
                self.on_resolved({"message_id": mid, "status": status, "kind": doc["message"]["kind"],
                                  "resolved_at": doc.get("resolved_at")})
            except Exception:
                log.exception("doctor_message_resolved observer failed")

    def _pending_doc(self, message_id: str) -> dict[str, Any]:
        doc = store.select_doctor_message(message_id)
        if doc is None:
            raise MessageError(404, "no such message")
        if doc["message"]["status"] != "pending":
            raise MessageError(409, f"message already {doc['message']['status']}")
        if bool(doc.get("is_demo", False)) != self.is_demo():
            raise MessageError(409, "this message belongs to the other mode; switch back to answer it")
        sender = self.pairings().get(doc["sender_id"])
        if sender is None or sender.status != "paired":  # revoked since it arrived: the instruction dies with the pairing
            self._resolve(doc, "expired")
            raise MessageError(410, "the doctor's pairing was revoked; nothing applied")
        if clock.now() >= datetime.fromisoformat(doc["expires_at"]):
            self._resolve(doc, "expired")
            raise MessageError(410, "message expired; nothing applied")
        return doc

    async def confirm(self, message_id: str) -> dict[str, Any]:
        """Fresh PIN on the device. Applies the kind, then posts the resolution."""
        doc = self._pending_doc(message_id)
        msg = DoctorMessage.model_validate(doc["message"])
        self._apply(msg, doc)
        self._resolve(doc, "confirmed")
        await self._announce_resolution(doc)
        return doc

    async def decline(self, message_id: str) -> dict[str, Any]:
        doc = self._pending_doc(message_id)
        self._resolve(doc, "declined")
        await self._announce_resolution(doc)
        return doc

    async def expire(self) -> int:
        """The relay client's tick: pending messages of THIS world past 24 h expire
        (nothing applied), and any resolution the relay has not yet taken is retried."""
        n = 0
        for doc in self.pending():
            if bool(doc.get("is_demo", False)) == self.is_demo() and clock.now() >= datetime.fromisoformat(doc["expires_at"]):
                self._resolve(doc, "expired")
                await self._announce_resolution(doc)
                n += 1
        for doc in store.select_doctor_messages():
            if doc["message"]["status"] != "pending" and not doc.get("resolution_posted", True):
                await self._announce_resolution(doc)
        return n

    # --- what confirm applies ---

    def _apply(self, msg: DoctorMessage, doc: dict[str, Any]) -> None:
        if msg.kind == "insulin_change":
            self._validate_kind(msg)
            now = clock.now()
            if msg.insulin == "basal":
                self.settings.basal_units = float(msg.new_units)
            # the change starts no earlier than the day it was confirmed: Follow-up compares the old dose's nights
            change = max(msg.start_date or now.date(), now.date())
            label = f"{msg.insulin} {msg.new_units:g} u from {change.isoformat()}"
            demo = bool(doc.get("is_demo", False))
            store.insert_treatment(Treatment(timestamp=now, kind="therapy_change", insulin_units=float(msg.new_units),
                                             dose_label=label, text=f"doctor message {msg.message_id}", confirmed=True),
                                   is_demo=demo)
            store.set_kv(therapy_change_key(demo), change.isoformat())
        elif msg.kind in ("plan_create", "plan_update", "proceed", "hold_step", "end_watch"):
            if self.on_plan_message is not None:
                self.on_plan_message(msg)  # R10 applies plans and holds; until then, recorded only
        # note, schedule_request, dismiss: nothing to apply
