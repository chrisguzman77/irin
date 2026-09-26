"""R3: the night ledger. At night-window end (the scheduler's "ledger" job on
clock.py, behind the NTP guard) write the NightRecord for the night just
ended: coverage, reason codes and their source, the overnight and dawn
rises, the low point, time below range, near-misses, level-2 excursions,
and the alarm episodes of the night. Every number and every reason code
comes from ml/models/nights.py through the adapter; nothing is computed
here. Idempotent: rebuilding a night replaces its row by night_date, and a
rebuilt night is identical. A demo night is badged is_demo."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Callable

from .. import store
from ..contracts import NightRecord
from .nights_adapter import NightInputs, NightsAdapter

log = logging.getLogger("irin.rounds.ledger")


def record_from(inp: NightInputs, codes: list[str], source: str, m: dict, is_demo: bool) -> NightRecord:
    start, end = inp.window
    return NightRecord(
        night_date=inp.night_date, window_start=start, window_end=end,
        coverage_pct=round(float(m["coverage_pct"]), 1), reason_codes=list(codes), code_source=source,
        rise_mgdl=m.get("rise_mgdl"), dawn_rise_mgdl=m.get("dawn_rise_mgdl"), low_point_mgdl=m.get("low_point_mgdl"),
        tbr_pct=m.get("tbr_pct"), minutes_below_70=int(m.get("minutes_below_70") or 0),
        near_miss_count=int(m.get("near_miss_count") or 0), level2_count=int(m.get("level2_count") or 0),
        alarm_event_ids=[e.event_id for e in inp.alarm_events if start <= e.started_at < end], is_demo=is_demo,
    )


@dataclass
class Ledger:
    adapter: NightsAdapter
    is_demo: Callable[[], bool] = lambda: False
    on_record: Callable[[NightRecord], None] | None = None  # R8's evaluation hangs here later

    def build_night(self, night_date: date) -> NightRecord:
        """Build (or rebuild) one night and store it. Raises when the inputs cannot
        be read; the scheduler job logs and the night is rebuilt by hand."""
        inp = self.adapter.night_inputs(night_date)
        codes, source = self.adapter.classify(inp)
        record = record_from(inp, codes, source, self.adapter.metrics(inp), self.is_demo())
        store.upsert_night_record(record)
        if self.on_record is not None:
            try:
                self.on_record(record)
            except Exception:
                log.exception("night record observer failed")
        return record
