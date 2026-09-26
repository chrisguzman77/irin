"""The ONE feature function (george.md "Shared feature code"), imported by
both build_dataset.py (training, vectorized pandas) and predict.py (the Pi,
numpy only). Never re-implement a feature in two places.

Features (~15, raw mg/dL, no normalization): current value; lags at
5/10/15/30/60 min; rate of change over 5/15/30 min; acceleration (change in
the 5-min rate between now and 15 min ago); rolling mean/std/min of the
window; sin/cos of hour + night-window flag. No insulin/carb features in v1.
The window is 13 readings (60 min at 5-min spacing), oldest first."""

from __future__ import annotations

import numpy as np

FEATURE_NAMES: list[str] = [
    "current", "lag5", "lag10", "lag15", "lag30", "lag60",
    "roc5", "roc15", "roc30", "accel",
    "roll_mean", "roll_std", "roll_min",
    "hour_sin", "hour_cos", "night_flag",
]
WINDOW_LEN = 13


def features(window: np.ndarray, hour: float, night_window: tuple[str, str] = ("22:00", "07:00")) -> np.ndarray:
    """window: the last WINDOW_LEN readings (mg/dL), oldest first. Returns a
    1-D array aligned with FEATURE_NAMES. TODO george.md step 2."""
    raise NotImplementedError("george.md step 2: features")
