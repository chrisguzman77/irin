"""The forecaster contract (george.md "predict.py contract"). Loads
forecast_v1.json once AT IMPORT (XGBoost JSON, never a pickle; xgboost pinned
identically in backend/ and ml/ requirements), so a missing or broken model
fails the backend's guarded import at boot instead of the first forecast.
predict(window) -> the model's 30-min-ahead value (delta added back). Features
come ONLY from features.py. No I/O per call, no globals mutated. numpy +
xgboost only.

WHAT THE NUMBER MEANS: forecast_v1 is a 20th-percentile (quantile 0.2)
forecast, "how low could glucose plausibly be in 30 min", not the most likely
value (metrics.md). The predicted-low warning compares it with 85 mg/dL
(PREDICTED_LOW_THRESHOLD, checkpoint 4); the actual-low alarm stays at 70.

The backend builds the window with features.latest_window(ts_sec, mgdl) over
its recent readings (at least the last HISTORY_MIN = 95 min): a (13,) slot
window with NaN for a dropped reading, or None = no forecast. It calls predict
only on a window it got back, and only when the newest reading is not stale."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .features import WINDOW_LEN, features, window_ok

MODEL_PATH = Path(__file__).resolve().parent / "forecast_v1.json"
QUANTILE = 0.2
PREDICTED_LOW_THRESHOLD = 85.0


def load_model():
    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"{MODEL_PATH} does not exist yet: train it with ml/train.py (george.md step 3)")
    import xgboost as xgb

    booster = xgb.Booster()
    booster.load_model(str(MODEL_PATH))
    return booster


_model = load_model()


def predict(window, hour: float = 0.0) -> float:
    """window: the (13,) slot window from features.latest_window (NaN = a
    dropped reading). hour: local hour of the newest reading."""
    w = np.asarray(window, dtype=float)
    if w.shape != (WINDOW_LEN,):
        raise ValueError(f"predict needs a ({WINDOW_LEN},) slot window, got shape {w.shape}")
    if not window_ok(w):
        raise ValueError("window not usable (newest reading missing or > 30 min without readings): no forecast")
    import xgboost as xgb

    x = features(w, hour).reshape(1, -1)
    delta = float(_model.predict(xgb.DMatrix(x))[0])       # NaN features route as XGBoost missing values
    return float(w[-1]) + delta
