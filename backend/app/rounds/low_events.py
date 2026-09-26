"""R4: nocturnal LowEvents (under 70 confirmed by 2 readings; nadir, burden,
recovery slope, carbs within 30 min, inferred_unfelt = >= 20 min under 70,
slope < 1.0 over 30 min, no carbs within 30 min; never on a treated low).
Computed via ml/models/nights.low_events."""

from __future__ import annotations


def detect(night_date) -> list:
    raise NotImplementedError("R4: low events")
