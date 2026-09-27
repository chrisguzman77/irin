"""B4+: the WhatsApp Cloud API channel (the second channel beside the
watcher page's voice loop).

POST /v0/buddy/whatsapp {phone} (a buddy pairing's bearer) registers the
buddy's own WhatsApp number (E.164) on that pairing. It is a contact detail:
stored on the pairing document only, never returned by any GET, never in
/v0/log, and deleted with the pairing's keys on revoke (invariant 15, 16).

POST /v0/buddy/notify {peer_id, first_name, minutes, audio_url, is_demo}
(X-Source-Key; the pairing must be this key's, peer_kind buddy, and of the
same world, invariant 18) sends, when a number is registered and
WHATSAPP_TOKEN + WHATSAPP_PHONE_NUMBER_ID are set: the buddy text ("Irin:
your buddy <first name> is in trouble. The alarm has been unacknowledged for
N minutes. Open https://watch.<DOMAIN>", prefixed [DEMO] for a demo pairing)
then, with an audio_url, the same ElevenLabs clip as an audio message, via
POST https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages. NEVER a
glucose value in a payload: the body forbids any field named like one, and
the only free text is a first name with no digits. Free-form messages reach
the buddy only inside WhatsApp's 24-hour window after they last messaged the
number (each buddy sends "hi" during setup). Logged: ids and outcomes only.

TRANSPORT is injectable so tests never reach the network."""

from __future__ import annotations

import logging
import os

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

import store
from relay_api import ID, require_bearer, require_source_key

log = logging.getLogger("irin.relay.notify")

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
DOMAIN = os.environ.get("DOMAIN", "irin-out-of-sleep-at-hackgt.tech")
GRAPH_URL = "https://graph.facebook.com/v21.0/{phone_number_id}/messages"
TRANSPORT: httpx.AsyncBaseTransport | None = None  # tests inject a MockTransport
FORBIDDEN_NOTIFY_FIELDS = ("glucose", "mgdl", "mg_dl", "location", "phone")

router = APIRouter(prefix="/v0/buddy")


class WhatsAppIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phone: str = Field(pattern=r"^\+[1-9]\d{7,14}$")  # E.164


class NotifyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    peer_id: str = Field(pattern=ID)
    first_name: str = Field(min_length=1, max_length=40, pattern=r"^[^\d@]{1,40}$")  # no digits: no number smuggled in
    minutes: int = Field(ge=0, le=1440)
    audio_url: str | None = Field(default=None, max_length=300, pattern=r"^https?://[A-Za-z0-9.\-:]+/v1/audio/[0-9a-f]{64}\.mp3$")
    is_demo: bool = False

    @model_validator(mode="before")
    @classmethod
    def _no_forbidden_fields(cls, data):
        if isinstance(data, dict):
            bad = [k for k in data if any(w in str(k).lower() for w in FORBIDDEN_NOTIFY_FIELDS)]
            if bad:
                raise ValueError(f"forbidden field(s) on a buddy notify: {bad} (invariant 15)")
        return data


def buddy_text(first_name: str, minutes: int, is_demo: bool) -> str:
    text = (f"Irin: your buddy {first_name} is in trouble. The alarm has been unacknowledged for {minutes} minutes. "
            f"Open https://watch.{DOMAIN}")
    return f"[DEMO] {text}" if is_demo else text


@router.post("/whatsapp")
async def register_whatsapp(req: WhatsAppIn, pairing: dict = Depends(require_bearer)) -> dict:
    """The buddy (their own bearer) registers their number; it is never read back."""
    if pairing.get("peer_kind") != "buddy":
        raise HTTPException(status_code=403, detail="WhatsApp alerts are for buddy pairings only")
    store.db()["pairings"].update_one({"_id": pairing["_id"]}, {"$set": {"whatsapp_phone": req.phone,
                                                                           "whatsapp_registered_at": store.now()}})
    store.audit("buddy.whatsapp", doctor_id=pairing["doctor_id"], is_demo=pairing["is_demo"])
    return {"registered": True}


async def _graph(client: httpx.AsyncClient, body: dict) -> bool:
    try:
        r = await client.post(GRAPH_URL.format(phone_number_id=WHATSAPP_PHONE_NUMBER_ID), json=body,
                              headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"})
        if r.status_code >= 400:
            log.warning("whatsapp graph call refused (%s)", r.status_code)
            return False
        return True
    except httpx.HTTPError as e:
        log.warning("whatsapp graph call failed (%s)", type(e).__name__)
        return False


@router.post("/notify")
async def notify(req: NotifyIn, source_key: str = Depends(require_source_key)) -> dict:
    """The Pi's best-effort second channel for one buddy the alert was sealed to."""
    pairing = store.db()["pairings"].find_one({"doctor_id": req.peer_id, "status": "confirmed"})
    if pairing is None:
        raise HTTPException(status_code=404, detail="no confirmed pairing for that peer")
    if pairing.get("source_key_hash") != store.bearer_hash(source_key):
        raise HTTPException(status_code=403, detail="not your pairing")
    if pairing.get("peer_kind") != "buddy":
        raise HTTPException(status_code=409, detail="notify goes to a buddy pairing only")
    if pairing["is_demo"] != req.is_demo:
        raise HTTPException(status_code=409, detail="is_demo does not match the pairing")
    phone = pairing.get("whatsapp_phone")
    if not phone:
        sent, reason = False, "no_number"
    elif not (WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID):
        sent, reason = False, "not_configured"
    else:
        to = phone.lstrip("+")
        async with httpx.AsyncClient(timeout=10.0, transport=TRANSPORT) as client:
            sent = await _graph(client, {"messaging_product": "whatsapp", "to": to, "type": "text",
                                         "text": {"body": buddy_text(req.first_name, req.minutes, req.is_demo),
                                                  "preview_url": True}})
            reason = "ok" if sent else "graph_error"
            if sent and req.audio_url and not await _graph(client, {"messaging_product": "whatsapp", "to": to,
                                                                    "type": "audio", "audio": {"link": req.audio_url}}):
                reason = "audio_failed"  # the text went out; the clip is still on the watcher page
    store.audit("buddy.notify", peer_id=req.peer_id, is_demo=req.is_demo, sent=sent, reason=reason,
                audio=req.audio_url is not None)
    return {"sent": sent, "reason": reason}
