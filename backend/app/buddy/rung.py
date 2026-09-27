"""B2: the buddy rung observer on alarm.py's on_transition hook, plus the
independent T+20 emergency-script clock.

Fires when a FULL alarm (active or rearmed) on an actual low has been
unacknowledged for RUNG_MIN minutes (10, BUDDY_RUNG_MIN in the environment,
clamped to 10-12) since the crossing (or since a re-arm: after an ack that is
still low 15 min later the rung re-climbs) AND the R2 recorder's running
presence verdict for the episode is home (in the room but unresponsive; an
empty room means the bathroom). brain_only has no presence, so it is
stricter: a confirmed level 2 low (two consecutive fresh readings under 54)
sustained RUNG_MIN minutes with zero acknowledgement in the episode, and the
alert is labeled unconfirmed.

On fire: {alert: BuddyAlert, listing: HubListing, message} (a first name and
minutes, NEVER a glucose value, invariant 15) is sealed to every paired buddy
of this world (demo only to demo pairings, invariant 18) and posted as an
envelope of kind buddy_alert, program buddy, when have_buddy is on; a hub
listing is posted when hub_watchable is on. Resolution on CGM recovery (the
engine closing the episode after two readings back at or above the low
threshold) or on ack: POST /v0/hub/resolve.

The emergency clock is armed when the rung fires, due 20 minutes after the
start of the unanswered stretch (the crossing, or the re-arm). It fires at
that time whatever any claim, call, or treating status says; only the
patient's own ack or the episode closing (recovery, a mode switch) disarms
it. Demo tier: a stored event, a WS line, and buddy_state.emergency.

ADDITIVE ONLY (invariant 13): the rung observes alarm.py and never calls into
it; nothing here delays, quiets, or gates a local alarm. Time is clock.py's.
Network sends are handed to `spawn` so the engine's observer call and the
tick never wait on the relay."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable

from .. import store
from ..alarm import Transition
from ..clock import clock
from ..contracts import AlarmState, BuddyAlert, HubListing, Pairing, Reading, Settings
from ..rounds import crypto

log = logging.getLogger("irin.buddy.rung")


def _rung_min() -> float:
    try:
        v = float(os.environ.get("BUDDY_RUNG_MIN", "10"))
    except ValueError:
        v = 10.0
    return min(12.0, max(10.0, v))


RUNG_MIN = _rung_min()
EMERGENCY_MIN = 20.0
LEVEL2_MGDL = 54.0
LEVEL2_READINGS = 2
FULL_STATES = ("active", "rearmed")
EPISODE_STATES = ("active", "acknowledged", "rearmed")
EMERGENCY_TEXT = "emergency contact alerted (simulated)"


def patient_first_name() -> str:
    """The name a buddy and the hub see (PATIENT_FIRST_NAME); nothing else about the patient."""
    return (os.environ.get("PATIENT_FIRST_NAME") or "Your buddy").strip()[:40]


def recorder_verdict(recorder: Any) -> str:
    """The R2 recorder's running presence_during verdict for the open episode."""
    ep = getattr(recorder, "open", None)
    return recorder._presence_during(ep) if ep is not None else "unknown"


@dataclass
class BuddyRung:
    settings: Settings
    alarm_state: Callable[[], AlarmState]
    recorder: Any  # the R2 AlarmEventRecorder: its open episode and running presence verdict
    recipients: Callable[[bool], list[Pairing]]  # pairing.recipients(is_demo)
    post_card: Callable[[dict], Awaitable[bool]]  # relay_client.post_card
    post_hub: Callable[[str, dict], Awaitable[bool]]  # relay_client.post_hub
    spawn: Callable[[Awaitable[Any]], None]
    device_id: str
    is_demo: Callable[[], bool] = lambda: False
    brain_only: Callable[[], bool] = lambda: False
    first_name: Callable[[], str] = patient_first_name
    on_alert: Callable[[dict], None] | None = None  # buddy_alert broadcast
    on_update: Callable[[dict], None] | None = None  # hub_update broadcast (resolved, emergency, call)
    # the episode
    crossing_at: datetime | None = None
    stretch_at: datetime | None = None  # start of the current unanswered stretch (crossing or re-arm)
    event_id: str | None = None
    acked: bool = False
    fired_stretch: bool = False
    climbs: int = 0
    alert: dict | None = None  # the open alert payload
    listing_id: str | None = None  # set only when a hub listing was posted
    emergency_due: datetime | None = None
    emergency: dict | None = None
    treating: dict | None = None
    # level 2 tracking (brain_only), from fresh readings
    l2_start: datetime | None = None
    l2_run: int = 0
    calls_seen: set = field(default_factory=set)
    _trigger: str | None = None

    # --- the observer (alarm.py calls observers bare: nothing here may reach the engine) ---

    def __call__(self, t: Transition) -> None:
        try:
            self._observe(t)
        except Exception:
            log.exception("buddy rung failed on %s -> %s", t.old_state, t.new_state)

    def _observe(self, t: Transition) -> None:
        if t.old_state == "idle":
            self._trigger = None  # a reset engine emits nothing; every episode starts from idle
            if self.crossing_at is not None:
                self._end("reset")
        if t.new_state == "idle":
            if self.crossing_at is not None:
                self._end("recovered")
            self._trigger = None
            return
        if t.trigger_type == "actual_low" and self._trigger != "actual_low":
            self._begin(t.at)
        self._trigger = t.trigger_type
        if self.crossing_at is None:
            return
        if t.new_state == "acknowledged" and t.ack_source is not None:
            self.acked = True
            self.stretch_at = None
            self.emergency_due = None  # the patient answered; claims, calls and treating never do this
            if self.alert is not None:
                self._resolve("acknowledged")
        elif t.new_state == "rearmed" and t.old_state != "rearmed":
            self.stretch_at = t.at  # still low 15 min after the ack: the rung re-climbs
            self.fired_stretch = False

    def on_reading(self, reading: Reading) -> None:
        """Every new reading (ws poll, after the engine): level 2 confirmation for brain_only."""
        try:
            if reading.is_stale:
                return
            if reading.glucose_mgdl < LEVEL2_MGDL:
                if self.l2_run == 0:
                    self.l2_start = reading.timestamp
                self.l2_run += 1
            else:
                self.l2_run, self.l2_start = 0, None
        except Exception:
            log.exception("buddy rung reading failed")

    # --- the clock (the 30 s alarm tick, after the recorder's radar sample) ---

    def tick(self) -> None:
        try:
            self._tick()
        except Exception:
            log.exception("buddy rung tick failed")

    def _tick(self) -> None:
        if self.crossing_at is None:
            return
        st = self.alarm_state()
        if not (st.state in EPISODE_STATES and st.trigger_type == "actual_low"):
            self._end("reset")  # the engine was reset under us (mode switch, scenario, seek)
            return
        now = clock.now()
        if self.emergency_due is not None and self.emergency is None and now >= self.emergency_due:
            self._fire_emergency(now)
        if self.alert is not None or self.fired_stretch or st.state not in FULL_STATES or self.stretch_at is None:
            return
        rung = timedelta(minutes=RUNG_MIN)
        if now - self.stretch_at < rung:
            return
        if self.brain_only():
            if not self.acked and self.l2_run >= LEVEL2_READINGS and self.l2_start is not None \
                    and now - self.l2_start >= rung:
                self._fire(now, "unconfirmed")
        elif recorder_verdict(self.recorder) == "home":
            self._fire(now, "device_confirmed")

    # --- the episode ---

    def _begin(self, at: datetime) -> None:
        self._end("reset")
        self.crossing_at = self.stretch_at = at
        ep = getattr(self.recorder, "open", None)
        started = ep.started_at if ep is not None else at
        self.event_id = f"ae-{started.strftime('%Y%m%dT%H%M%S%f')}-actual_low"  # the AlarmEvent it becomes

    def _end(self, outcome: str) -> None:
        if self.alert is not None:
            self._resolve(outcome)
        self.crossing_at = self.stretch_at = self.event_id = None
        self.acked = self.fired_stretch = False
        self.climbs = 0
        self.emergency_due = self.emergency = self.treating = None

    def reset(self) -> None:
        """A mode switch: the open episode ends; nothing new is sent but the resolve."""
        self._end("reset")
        self._trigger = None
        self.l2_run, self.l2_start = 0, None

    def record(self, kind: str, confidence: str | None = None) -> None:
        """buddy_events for the cloud forwarder: never a glucose value."""
        try:
            store.insert_buddy_event({"event_id": self.event_id, "kind": kind, "at": clock.now().isoformat(),
                                      "confidence": confidence or (self.alert or {}).get("alert", {}).get("confidence"),
                                      "is_demo": self.is_demo()})
        except Exception:
            log.exception("buddy event not stored")

    def _fire(self, now: datetime, confidence: str) -> None:
        self.fired_stretch = True
        self.climbs += 1
        demo = self.is_demo()
        name = self.first_name()
        minutes = int((now - self.stretch_at).total_seconds() // 60)
        alert = BuddyAlert(alert_id=f"ba-{self.event_id}-{self.climbs}", event_id=self.event_id, urgency=self.climbs,
                           confidence=confidence, created_at=now, status="open", audio_url=None)
        listing = HubListing(listing_id=f"hl-{alert.alert_id}", first_name=name, confidence=confidence,
                             elapsed_min=minutes, urgency=self.climbs, is_demo=demo)
        self.alert = {"alert": alert.model_dump(mode="json"), "listing": listing.model_dump(mode="json"),
                      "message": f"{name}'s low alarm has gone unanswered for {minutes} minutes."}
        if self.emergency is None:
            self.emergency_due = self.stretch_at + timedelta(minutes=EMERGENCY_MIN)
        self.record("alert", confidence)
        nb = self.settings.night_buddy
        if nb.have_buddy:
            self.spawn(self._deliver(dict(self.alert), demo, "irin_brain" if self.brain_only() else "irin_bedside"))
        if nb.hub_watchable:
            self.listing_id = listing.listing_id
            body = {"listing_id": listing.listing_id, "first_name": name, "confidence": confidence,
                    "elapsed_min": minutes, "urgency": listing.urgency, "is_demo": demo, "event_id": self.event_id}
            steps = self.settings.emergency_script.steps if self.settings.emergency_script else []
            if steps:
                # plain JSON over the key-gated HTTPS route; the relay encrypts it at rest with its own key
                # and releases it only to the live claim-holder (B3)
                body["script"] = {"steps": list(steps)}
            self.spawn(self.post_hub("listing", body))
        if self.on_alert is not None:
            try:
                self.on_alert(self.alert)
            except Exception:
                log.exception("buddy_alert observer failed")

    async def _deliver(self, payload: dict, demo: bool, source: str) -> int:
        """Seal to every paired buddy of this world (a demo alert only to a demo pairing)."""
        sent = 0
        for p in self.recipients(demo):
            if p.peer_kind != "buddy" or p.is_demo != demo:
                continue
            sealed = crypto.seal(json.dumps(payload), p.doctor_pk)
            env = {"recipient_id": p.doctor_id, "sender_id": self.device_id, "nonce": sealed["nonce"],
                   "ciphertext": sealed["ciphertext"], "source": source, "kind": "buddy_alert", "program": "buddy",
                   "is_demo": demo, "card_id": payload["alert"]["alert_id"]}
            if await self.post_card(env):
                sent += 1
        return sent

    def _resolve(self, outcome: str) -> None:
        payload, self.alert = self.alert, None
        self.record("resolved", payload["alert"]["confidence"] if payload else None)
        listing_id, self.listing_id = self.listing_id, None
        if listing_id is not None:
            self.spawn(self.post_hub("resolve", {"listing_id": listing_id, "outcome": outcome}))
        self.treating = None
        self._update({"listing_id": payload["listing"]["listing_id"] if payload else listing_id,
                      "event": "resolved", "outcome": outcome})

    def _fire_emergency(self, now: datetime) -> None:
        self.emergency = {"at": now.isoformat(), "text": EMERGENCY_TEXT, "event_id": self.event_id,
                          "is_demo": self.is_demo()}
        self.record("emergency")
        self._update({"event": "emergency", **self.emergency})

    def _update(self, payload: dict) -> None:
        if self.on_update is not None:
            try:
                self.on_update(payload)
            except Exception:
                log.exception("hub_update observer failed")

    # --- the snapshot ---

    def link(self) -> dict | None:
        demo = self.is_demo()
        for p in self.recipients(demo):
            if p.peer_kind == "buddy":
                # mode arrives with directory matching (B3+); a pre-matched demo pair is a twin
                return {"first_name": p.doctor_display_name, "mode": "twin", "peer_id": p.doctor_id}
        return None

    def current_treating(self) -> dict | None:
        if self.treating is None or clock.now() >= datetime.fromisoformat(self.treating["expires_at"]):
            return None
        return self.treating

    def state(self) -> dict:
        try:
            link = self.link()
        except Exception:
            link = None
        return {"link": link, "open_alert": self.alert, "treating": self.current_treating(), "morning_line": None,
                "emergency": self.emergency}
