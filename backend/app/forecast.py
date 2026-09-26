"""Forecast wrapper (chris.md step 6): calls ml/models/predict.py on every new
reading; suspended when the last hour has a gap or stale data (invariant 1:
never forecast on stale/gapped data). The xgboost import is guarded so the
backend boots without the model or the OpenMP runtime."""

from __future__ import annotations

from .contracts import Forecast, Reading

try:  # ml/models/predict.py is George's; it needs numpy + xgboost + forecast_v1.json
    from ml.models import predict as _predict  # type: ignore  # noqa: F401
    PREDICT_AVAILABLE = True
except Exception:  # ImportError, XGBoostError (missing libomp on macOS), missing model
    _predict = None
    PREDICT_AVAILABLE = False


def forecast(window: list[Reading]) -> Forecast | None:
    """None when suspended (gap or stale in the window). TODO step 6."""
    raise NotImplementedError("step 6: forecast wrapper over ml/models/predict.py")
