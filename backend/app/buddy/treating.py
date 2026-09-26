"""B4: the patient side. POST /api/buddy/treating (PIN, one thumb press from
the app) -> relay treating -> treating_set broadcast; the four opt-ins in
Settings.night_buddy; the Pi's relay poll picks up brokered "call" events and
plays buddy_chime through hal (never an alarm tone)."""

from __future__ import annotations


async def set_treating() -> None:
    raise NotImplementedError("B4: treating")
