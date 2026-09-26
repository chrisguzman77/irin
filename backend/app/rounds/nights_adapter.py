"""R3: the adapter between store.py and ml/models/nights.py: gathers the
readings, treatments, alarm events, and raw presence for one night and calls
classify_night / night_metrics. Under IRIN_BRAIN_ONLY it ignores presence,
alarm hardware events, and logged context; rows change confidence label
(reported / inferred) and are never blanked (R14c)."""

from __future__ import annotations


def night_inputs(night_date, brain_only: bool = False) -> dict:
    raise NotImplementedError("R3: nights adapter")
