"""R11: morning recall. One LowEventRecall per LowEvent at night-window end,
capped at the two deepest; recall_due broadcast; POST
/api/rounds/recall/{low_event_id} (PIN); unanswered at noon = "no answer",
never fine; unfelt_low_rate = unfelt / answered."""

from __future__ import annotations


def create_recalls(night_date) -> list:
    raise NotImplementedError("R11: morning recall")
