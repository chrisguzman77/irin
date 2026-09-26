"""The forecaster contract (george.md "predict.py contract"). Loads
forecast_v1.json once at import (XGBoost JSON, never a pickle; xgboost pinned
identically in backend/ and ml/ requirements); predict(window) -> the
predicted 30-min-ahead value (delta added back). Features come ONLY from
features.py. No I/O per call, no globals mutated. numpy + xgboost only."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .features import WINDOW_LEN, features

MODEL_PATH = Path(__file__).resolve().parent / "forecast_v1.json"
_model = None


def load_model():
    global _model
    if _model is None:
        if not MODEL_PATH.exists():
            raise FileNotFoundError(f"{MODEL_PATH} does not exist yet: train it with ml/train.py (george.md step 3)")
        import xgboost as xgb

        booster = xgb.Booster()
        booster.load_model(str(MODEL_PATH))
        _model = booster
    return _model


def predict(window: list[float], hour: float = 0.0) -> float:
    if len(window) != WINDOW_LEN:
        raise ValueError(f"predict needs exactly {WINDOW_LEN} readings, got {len(window)}")
    import xgboost as xgb

    model = load_model()
    x = features(np.asarray(window, dtype=float), hour).reshape(1, -1)
    delta = float(model.predict(xgb.DMatrix(x))[0])
    return window[-1] + delta
