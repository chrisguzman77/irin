"""Insulin on board (chris.md step 5).

A single dose decays along a curvilinear curve that reaches EXACTLY zero at
`duration_hours` (default Settings.iob_duration_hours = 4.0): the fraction
still active t hours after a dose is (1 - t / D) ** 2 for 0 <= t < D and 0
from D on. IOB is the sum over confirmed bolus treatments; basal, carbs,
notes, GLP-1 doses, and therapy changes never count. A dose timestamped
after `at` has not happened yet and contributes nothing.

Absurd inputs raise instead of producing a quiet wrong number: negative or
non-finite units, a duration that is not a positive finite number.
"""

from __future__ import annotations

import math
from datetime import datetime

from .contracts import Treatment

DEFAULT_DURATION_HOURS = 4.0


def fraction_remaining(hours_since_dose: float, duration_hours: float = DEFAULT_DURATION_HOURS) -> float:
    """Share of a dose still active `hours_since_dose` after it (1.0 at the dose, 0.0 at duration)."""
    if not (math.isfinite(duration_hours) and duration_hours > 0):
        raise ValueError(f"duration_hours must be a positive finite number, got {duration_hours!r}")
    if not math.isfinite(hours_since_dose):
        raise ValueError(f"hours_since_dose must be finite, got {hours_since_dose!r}")
    if hours_since_dose < 0:
        return 0.0  # a dose in the future has not happened yet
    if hours_since_dose >= duration_hours:
        return 0.0
    x = 1.0 - hours_since_dose / duration_hours
    return x * x


def dose_iob(units: float, hours_since_dose: float, duration_hours: float = DEFAULT_DURATION_HOURS) -> float:
    if not math.isfinite(units) or units < 0:
        raise ValueError(f"insulin units must be a non-negative finite number, got {units!r}")
    return units * fraction_remaining(hours_since_dose, duration_hours)


def iob(treatments: list[Treatment], at: datetime, duration_hours: float = DEFAULT_DURATION_HOURS) -> float:
    """Total insulin on board at `at` from confirmed bolus treatments (units)."""
    total = 0.0
    for t in treatments:
        if t.kind != "bolus" or t.insulin_units is None or not t.confirmed:
            continue
        hours = (at - t.timestamp).total_seconds() / 3600.0
        total += dose_iob(t.insulin_units, hours, duration_hours)
    return total
