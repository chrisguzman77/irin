"""R9: doctor messages. Incoming DoctorMessage must open with the paired
doctor's key; stored pending; doctor_message_received broadcast; POST
/api/rounds/messages/{id}/confirm | decline (fresh PIN); confirm applies the
kind (insulin_change updates Settings.basal_units, logs one therapy_change
Treatment, starts a Follow-up; plan_create/update; proceed; hold_step; ...);
decline and 24 h expiry apply nothing; the resolution word is posted to the
relay (invariant 8)."""

from __future__ import annotations


def confirm(message_id: str) -> None:
    raise NotImplementedError("R9: doctor messages")
