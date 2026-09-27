"""B4: the patient side. POST /api/buddy/treating (PIN, one thumb press from
the app) -> {since, expires_at} (20 min) -> relay /v0/hub/treating when a
listing is open -> treating_set broadcast (main.py). Treating never touches
the alarm or the T+20 emergency clock (invariant 13). The four opt-ins live
in Settings.night_buddy (POST /api/settings merges them).

The Pi's relay poll hands brokered "call" events here: each plays the buddy
chime once through the output gate (never an alarm tone). The sound driver
has ONE channel (a new play() stops the previous sound), so while a low alarm
is sounding the chime is NOT played: the alarm keeps sounding and the call
is still broadcast and recorded (chimed: false)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Callable

from ..clock import clock
from .rung import BuddyRung

TREATING_MIN = 20
BUDDY_CHIME = "buddy_chime"  # hardware/hal.py SOUND_NAMES; plays once, never an alarm tone
SOUNDING_STATES = ("pending", "active", "rearmed")
LOW_TIERS = ("predicted_low", "actual_low")


class TreatingError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status, self.detail = status, detail


def set_treating(rung: BuddyRung) -> dict:
    if rung.alert is None:
        raise TreatingError(409, "no buddy alert is open")
    now = clock.now()
    rung.treating = {"since": now.isoformat(), "expires_at": (now + timedelta(minutes=TREATING_MIN)).isoformat()}
    rung.record("treating")
    if rung.listing_id is not None:
        rung.spawn(rung.post_hub("treating", {"listing_id": rung.listing_id}))
    return dict(rung.treating)


def handle_calls(rung: BuddyRung, calls: list[dict], outputs: Any, alarm_state: Callable[[], Any],
                 volume: float) -> list[dict]:
    """Each new brokered call: the chime once (unless a low alarm is sounding), a
    recorded event, and a hub_update {listing_id, event: "call"}. Returns the updates."""
    out = []
    for c in calls or []:
        key = (c.get("listing_id"), c.get("claim_id"), c.get("at"))
        if key in rung.calls_seen:
            continue
        rung.calls_seen.add(key)
        st = alarm_state()
        sounding = st.state in SOUNDING_STATES and st.trigger_type in LOW_TIERS
        if not sounding:
            outputs.play_sound(BUDDY_CHIME, volume)
        rung.record("call")
        update = {"listing_id": c.get("listing_id"), "event": "call", "chimed": not sounding}
        rung._update(update)
        out.append(update)
    return out
