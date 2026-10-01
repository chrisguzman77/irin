"""R7: SignalCard assembly and delivery. assemble() builds a card from an
evaluation (program, kind, status, flags, metrics with their confidence map,
nights, excluded counts, tolerance days, allowed actions, resources), with a
headline and a deterministic template narrative (R13 puts narrative.py's
chain, with the validator, in front of the template). card_id derives from
(program, kind, period, plan_id, step_index) so a re-evaluation never
duplicates a card. A card never contains a dose recommendation, every metric
row carries a confidence label, and no row is blank (invariant 7).

CardSender seals a card to every paired peer of the card's kind of world
(demo cards only to demo pairings, invariant 11), posts it through the relay
client, stores it, and broadcasts card_sent; an undelivered card is kept and
retried on the relay client's next tick."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

from .. import store
from ..clock import clock
from ..contracts import SignalCard
from . import crypto

log = logging.getLogger("irin.rounds.cards")

LABELS = ("measured", "reported", "inferred")
UNLABELED = {"nights", "excluded_nights", "window_low_point", "baseline_low_point", "insufficient"}  # counts and lists
ACTIONS = {
    "basal_check": ["adjust_basal", "schedule_visit", "ask_patient", "dismiss"],
    "hypo_response": ["message", "schedule_visit", "resources", "dismiss"],
    "follow_up": ["message", "schedule_visit", "dismiss"],
    "early_check": ["proceed", "hold_step", "adjust_insulin", "message", "end_watch"],
    "step_check": ["proceed", "hold_step", "adjust_insulin", "message", "end_watch"],
    "step_gate": ["proceed", "hold_step", "adjust_insulin", "message", "end_watch"],
    "safety": ["message", "hold_step", "adjust_insulin", "schedule_visit", "end_watch"],
    "graduation": ["message", "dismiss"],
    "baseline_note": ["dismiss"],
}


def card_id(program: str, kind: str, period_start: date, period_end: date,
            plan_id: str | None = None, step_index: int | None = None) -> str:
    parts = [program, kind]
    if plan_id is not None:
        parts += [plan_id, str(step_index if step_index is not None else "")]
    parts += [period_start.isoformat(), period_end.isoformat()]
    return ":".join(parts)


def pseudonym(device_id: str) -> str:
    """A stable, meaningless patient label for the inbox (no name, no id)."""
    return f"Patient {int.from_bytes(hashlib.sha256(device_id.encode()).digest()[:2], 'big') % 90 + 10}"


def template_narrative(kind: str, headline: str, metrics: dict, confidence: dict) -> str:
    """The deterministic narrative: the headline, then each labeled number in
    plain words. No advice, no dose, nothing the metrics do not say."""
    rows = []
    for key, value in metrics.items():
        if key in UNLABELED or isinstance(value, (dict, list)) or value is None or isinstance(value, bool):
            continue
        shown = f"{round(value, 1):g}" if isinstance(value, float) else str(value)
        rows.append(f"{key.replace('_', ' ')} {shown} ({confidence.get(key, 'inferred')})")
    body = "; ".join(rows)
    return f"{headline} Computed by Irin from the device's own record: {body}." if body else headline


def assemble(*, program: str, kind: str, status: str, flags: list[str], metrics: dict, confidence: dict,
             period_start: date, period_end: date, headline: str, device_id: str, is_demo: bool,
             source: str = "irin_bedside", nights: list[dict] | None = None, excluded_counts: dict | None = None,
             tolerance_days: list[dict] | None = None, resource_categories: list[str] | None = None,
             plan_id: str | None = None, step_index: int | None = None, narrative: str | None = None) -> SignalCard:
    """Every metric row labeled and non-blank; a metric that cannot be computed
    (None) is dropped from the card rather than shown blank; allowed_actions
    are the kind's verbs (never a number)."""
    clean: dict[str, Any] = {}
    labels: dict[str, str] = {}
    for key, value in metrics.items():
        if value is None:
            continue
        clean[key] = value
        if key not in UNLABELED:
            label = confidence.get(key, "inferred")
            labels[key] = label if label in LABELS else "inferred"
    card = SignalCard(
        card_id=card_id(program, kind, period_start, period_end, plan_id, step_index), program=program, kind=kind,
        status=status, flags=list(flags), confidence=labels, source=source, patient_pseudonym=pseudonym(device_id),
        plan_id=plan_id, step_index=step_index, period_start=period_start, period_end=period_end, headline=headline,
        metrics=clean, nights=[{k: v for k, v in n.items() if v is not None} for n in (nights or [])],
        excluded_counts=dict(excluded_counts or {}), tolerance_days=list(tolerance_days or []),
        narrative=narrative or template_narrative(kind, headline, clean, labels),
        allowed_actions=list(ACTIONS.get(kind, ["dismiss"])), resource_categories=list(resource_categories or []),
        is_demo=is_demo, generated_at=clock.now(),
    )
    return card


def _checkable(card: SignalCard) -> dict:
    """The numbers a card narrative may use: the card's metrics, plus any number the
    code-computed headline states that no metric already holds (e.g. the 5 in "rough
    stomach 3 of 5 days", a count of days). A number already in the metrics keeps
    its own sign, so "rose 22" still fails when the metric is -22. Not stored."""
    from .narrative import numeric_tokens

    def values(v):
        if isinstance(v, dict):
            for x in v.values():
                yield from values(x)
        elif isinstance(v, list):
            for x in v:
                yield from values(x)
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            yield abs(float(v))

    known = set(values(card.metrics))
    extra = []
    for tok in numeric_tokens(card.headline):
        try:
            x = float(tok.replace(",", ""))
        except ValueError:
            continue  # clock times and ranges are not metric values
        if x not in known:
            extra.append(x)
    return {**card.metrics, "headline_numbers": extra} if extra else dict(card.metrics)


async def with_narrative(card: SignalCard) -> SignalCard:
    """R13: the card's narrative through narrative.py's chain (the validator and the
    second opinion run inside it; any miss ships the deterministic template), in a
    worker thread so a slow model never stalls the loop, the poller or an alarm.
    Memory off: a card carries no scope. The numbers are the card's own labeled metrics."""
    import asyncio

    from .narrative import generate

    try:
        text = await asyncio.to_thread(generate, "card", {"kind": card.kind, "headline": card.headline,
                                                          "confidence": dict(card.confidence)}, _checkable(card))
    except Exception:
        log.exception("card narrative failed; the template stands")
        return card
    return card.model_copy(update={"narrative": text}) if text else card


def envelope(card: SignalCard, recipient_id: str, doctor_pk: str, device_id: str, synthetic: bool = False) -> dict[str, Any]:
    """`synthetic` rides as an extra key in the sealed payload (not a contracts field) so the inbox can badge it."""
    body = json.dumps({**card.model_dump(mode="json"), "synthetic": True}) if synthetic else card.model_dump_json()
    sealed = crypto.seal(body, doctor_pk)
    return {"recipient_id": recipient_id, "sender_id": device_id, "nonce": sealed["nonce"],
            "ciphertext": sealed["ciphertext"], "source": card.source, "kind": card.kind, "program": card.program,
            "is_demo": card.is_demo, "card_id": card.card_id}


@dataclass
class CardSender:
    recipients: Callable[[bool], list]  # pairing.recipients(is_demo) -> paired peers
    post: Callable[[dict], Any]  # relay_client.post_card (async)
    device_id: str
    on_sent: Callable[[dict], None] | None = None  # card_sent broadcast
    pending: dict[str, SignalCard] = field(default_factory=dict)  # undelivered, retried on the relay tick
    event_keys: dict[str, str | None] = field(default_factory=dict)  # card_id -> the red's event, kept for retries
    synthetic: Callable[[], bool] = lambda: False  # the active replay scenario is synthetic
    synthetic_ids: set[str] = field(default_factory=set)  # cards built while it was: retries keep the flag

    def __post_init__(self) -> None:
        try:  # a restart keeps retrying what the relay never took
            for doc in store.select_cards(limit=500):
                if doc.get("status") == "unsent":
                    self.pending[doc["card"]["card_id"]] = SignalCard.model_validate(doc["card"])
                    self.event_keys[doc["card"]["card_id"]] = doc.get("event_key")
        except Exception:
            log.exception("could not reload unsent cards")

    async def send(self, card: SignalCard, event_key: str | None = None) -> dict[str, Any]:
        """Seal to every paired peer of the card's world and post. Stored either
        way (with the red's event key, so the budget can deduplicate after a
        restart); a failed post is retried on the next relay tick."""
        if event_key is not None:
            self.event_keys[card.card_id] = event_key
        event_key = self.event_keys.get(card.card_id)
        if card.is_demo and self.synthetic():
            self.synthetic_ids.add(card.card_id)
        peers = self.recipients(card.is_demo)
        delivered, failed = [], []
        for p in peers:
            ok = await self.post(envelope(card, p.doctor_id, p.doctor_pk, self.device_id,
                                         card.card_id in self.synthetic_ids))
            (delivered if ok else failed).append(p.doctor_id)
        status = "sent" if delivered and not failed else "unsent" if peers else "no_recipient"
        store.upsert_card(card, status=status, recipients=delivered, event_key=event_key)
        if failed:
            self.pending[card.card_id] = card
        elif card.card_id in self.pending:
            del self.pending[card.card_id]
        result = {"card_id": card.card_id, "kind": card.kind, "program": card.program, "status": status,
                  "recipients": delivered, "is_demo": card.is_demo}
        if delivered and self.on_sent is not None:
            try:
                self.on_sent(result)
            except Exception:
                log.exception("card_sent observer failed")
        return result

    async def flush(self) -> int:
        """Retry every undelivered card (the relay client's tick)."""
        n = 0
        for card in list(self.pending.values()):
            if (await self.send(card))["status"] == "sent":
                n += 1
        return n
