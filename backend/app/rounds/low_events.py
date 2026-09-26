"""R4: nocturnal LowEvents. Each low under 70 inside the night window,
confirmed by 2 consecutive readings (a new event needs 2 readings back at
or above 70 in between), becomes a LowEvent: started_at, nadir_mgdl,
nadir_at, minutes_below_70, auc_below_70, recovery_slope over the 30 min
after the nadir, carbs_logged_within_30min, inferred_unfelt (a run of at
least 20 min under 70 whose recovery slope is under 1.0 mg/dL/min with no
carbs within 30 min, never on a treated low), alarm_event_id, is_demo.
Computed by ml/models/nights.low_events through the adapter; stored by
low_event_id (idempotent), built with the night ledger."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Callable

from .. import store
from ..contracts import LowEvent
from .nights_adapter import NightsAdapter

log = logging.getLogger("irin.rounds.low_events")


@dataclass
class LowEventDetector:
    adapter: NightsAdapter
    is_demo: Callable[[], bool] = lambda: False
    on_event: Callable[[LowEvent], None] | None = None  # R11 recall asks its morning question from here

    def detect(self, night_date: date) -> list[LowEvent]:
        """The night's LowEvents, stored (a rebuild replaces each by id)."""
        inp = self.adapter.night_inputs(night_date)
        events = [LowEvent.model_validate({**row, "is_demo": self.is_demo()}) for row in self.adapter.lows(inp)]
        store.replace_low_events(night_date, events)  # a rebuild after a re-cached reading leaves no stale row
        for event in events:
            if self.on_event is not None:
                try:
                    self.on_event(event)
                except Exception:
                    log.exception("low event observer failed")
        return events
