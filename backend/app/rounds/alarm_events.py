"""R2: the AlarmEvent recorder. An observer on alarm.py's on_transition hook
that writes one AlarmEvent per EPISODE (one continuous non-idle run of the
engine): tier = the highest tier the episode reached (actual_low >
predicted_low > stale > high), started_at, acknowledged_at and ack_source
(the first ack), escalated (any escalation before the first ack: the
warning going active by timeout or by crossing, or the 5-minute strobe
step), rearm_count, crossed_actual (the episode reached actual_low; a
predicted_low episode with crossed_actual False is a near-miss), and
presence_during, is_demo.

presence_during is the RAW radar aggregated ONCE, here (decision B7): the
tick loop hands every 30 s sample to sample(); "home" if a majority of the
episode's samples detect someone OR any detection falls within 2 minutes
around the alarm start (a 2-minute ring buffer before the start counts
too); "away" if nothing was detected across the episode (one stray blip
in an otherwise empty room still reads away); "unknown" otherwise: an
undriven mock, a hal error, or brain_only. It is a verdict from the raw
radar, never PresenceState.mode (which the night rule pins to Home).

The recorder OBSERVES only: it never calls into alarm.py. Stored in
store.alarm_events by event_id; a mode or scenario switch drops the open
episode (reset()) and writes nothing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

from .. import store
from ..alarm import Transition
from ..clock import clock
from ..contracts import AlarmEvent

log = logging.getLogger("irin.rounds.alarm_events")

TIER_RANK = {"high": 1, "stale": 2, "predicted_low": 3, "actual_low": 4}
LOW_TIERS = ("predicted_low", "actual_low")
START_WINDOW = timedelta(minutes=2)  # any detection this close to the alarm start = home
PRE_START_BUFFER = timedelta(minutes=2)  # samples kept from before an episode opens
BLIP_MAX = 1  # detections tolerated in an otherwise empty room (a radar blip)


@dataclass
class _Episode:
    started_at: datetime
    tier: str
    acknowledged_at: datetime | None = None
    ack_source: str | None = None
    escalated: bool = False
    rearm_count: int = 0
    crossed_actual: bool = False
    samples: list[tuple[datetime, bool | None]] = field(default_factory=list)


@dataclass
class AlarmEventRecorder:
    is_demo: Callable[[], bool] = lambda: False
    brain_only: Callable[[], bool] = lambda: False
    on_event: Callable[[AlarmEvent], None] | None = None  # observers of finished episodes (R3 ledger, B2)
    open: _Episode | None = None
    recent: list[tuple[datetime, bool | None]] = field(default_factory=list)  # the pre-start ring buffer
    events: list[AlarmEvent] = field(default_factory=list)  # this process's finished episodes, oldest first

    # --- the raw radar (hal.get_presence(), sampled every 30 s of clock time by the tick loop) ---

    def sample(self, raw: bool | None) -> None:
        now = clock.now()
        if self.open is not None:
            self.open.samples.append((now, raw))
        else:
            self.recent.append((now, raw))
            cutoff = now - PRE_START_BUFFER
            self.recent = [s for s in self.recent if s[0] >= cutoff]

    # --- the observer ---

    def __call__(self, t: Transition) -> None:
        try:
            self._observe(t)
        except Exception:  # alarm.py calls observers bare: nothing here may ever reach the engine
            log.exception("alarm event recorder failed on %s -> %s", t.old_state, t.new_state)

    def _open(self, t: Transition) -> None:
        self.open = _Episode(started_at=t.at, tier=t.trigger_type or "high")
        self.open.samples = [s for s in self.recent if s[0] >= t.at - PRE_START_BUFFER]
        self.recent = []

    def _observe(self, t: Transition) -> None:
        if t.old_state == "idle" and t.new_state != "idle":
            if self.open is not None:  # cannot happen through the hook; never lose an episode
                self._close(t.at)
            self._open(t)
        elif (self.open is not None and t.trigger_type in LOW_TIERS and self.open.tier not in LOW_TIERS
              and t.new_state != "idle"):
            # a low replaces a high or stale indicator: alarm.py starts a fresh episode there, so do we
            self._close(t.at)
            self._open(t)
        ep = self.open
        if ep is None:
            return
        if t.trigger_type and TIER_RANK.get(t.trigger_type, 0) > TIER_RANK.get(ep.tier, 0):
            ep.tier = t.trigger_type
            if t.trigger_type == "actual_low" and ep.acknowledged_at is None:
                ep.escalated = True  # the warning crossed into the full alarm before anyone answered
        if t.trigger_type == "actual_low":
            ep.crossed_actual = True
        if t.escalated and ep.acknowledged_at is None:
            ep.escalated = True
        if t.ack_source is not None and ep.acknowledged_at is None:
            ep.acknowledged_at, ep.ack_source = t.at, t.ack_source
        if t.new_state == "rearmed" and t.old_state != "rearmed":
            ep.rearm_count += 1
        if t.new_state == "idle":
            self._close(t.at)

    def reset(self) -> None:
        """A mode or scenario switch: the open episode is dropped, nothing written."""
        self.open = None
        self.recent = []

    # --- the verdict and the write ---

    def _presence_during(self, ep: _Episode) -> str:
        """B7 as coded: a detection within 2 min of the start, or detections in
        at least half the known samples (a tie reads home), = home; no
        detection, or a single blip among 4+ samples, = away; else unknown."""
        if self.brain_only():
            return "unknown"
        known = [(at, v) for at, v in ep.samples if v is not None]
        if not known:
            return "unknown"
        detections = [at for at, v in known if v]
        if any(abs(at - ep.started_at) <= START_WINDOW for at in detections):
            return "home"
        if len(detections) * 2 >= len(known):
            return "home"
        if len(detections) <= BLIP_MAX and len(known) >= 4:
            return "away"
        if not detections:
            return "away"
        return "unknown"

    def _close(self, at: datetime) -> None:
        ep, self.open = self.open, None
        if ep is None:
            return
        event = AlarmEvent(event_id=f"ae-{ep.started_at.strftime('%Y%m%dT%H%M%S%f')}-{ep.tier}", tier=ep.tier,
                           started_at=ep.started_at, acknowledged_at=ep.acknowledged_at, ack_source=ep.ack_source,
                           escalated=ep.escalated, rearm_count=ep.rearm_count, crossed_actual=ep.crossed_actual,
                           presence_during=self._presence_during(ep), is_demo=self.is_demo())
        try:
            store.upsert_alarm_event(event)
        except Exception:  # the recorder never takes the engine down
            log.exception("alarm event not stored")
        self.events.append(event)
        if self.on_event is not None:
            try:
                self.on_event(event)
            except Exception:
                log.exception("alarm event observer failed")
