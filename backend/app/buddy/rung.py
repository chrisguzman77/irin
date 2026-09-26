"""B2: the buddy rung observer on alarm.py's on_transition hook. Fires when
a FULL alarm has been unacknowledged for T+10 min after the actual-low
crossing AND the recorder's running presence_during verdict is home
(brain_only: confirmed level 2 low sustained 10 min, zero phone interaction,
alert labeled unconfirmed). On fire: seal a BuddyAlert to the buddy's key and
POST it; open a hub listing if hub_watchable. The T+20 emergency-script clock
is an INDEPENDENT timer on clock.py. ADDITIVE ONLY: never delays, quiets, or
gates any local alarm (invariant 13). Demo alerts go only to demo pairings."""

from __future__ import annotations


class BuddyRung:
    def __call__(self, transition) -> None:  # registered via engine.on_transition
        raise NotImplementedError("B2: buddy rung")
