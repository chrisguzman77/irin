"""Forecast wrapper (chris.md step 6): calls George's forecaster on every new
reading and SUSPENDS when the last hour is not usable (invariant 1: never
forecast on stale or gapped data).

The window rule is George's features.latest_window over the recent readings
(at least the last 75 min, sorted, POSIX seconds): it returns the (13,) slot
window ending on the newest reading, NaN for a dropped reading, or None when
the window is not usable (a gap of more than 30 min, or the newest slot
missing). Never a private copy of that rule. Staleness of the newest reading
is the backend's rule: a stale newest reading suspends before the window is
even built. The predictor is injectable so tests need no model; without
forecast_v1.json (or without xgboost's OpenMP runtime on macOS) the wrapper
reports status "unavailable" and the engine simply gets no forecasts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

import numpy as np

from . import config as _config  # noqa: F401  (puts the repo root on sys.path so `ml` imports)
from .contracts import Forecast, Reading

log = logging.getLogger("irin.forecast")
PredictFn = Callable[[np.ndarray, float], float]  # (slot window, hour) -> predicted mg/dL

try:
    from ml.models.features import HISTORY_MIN as HISTORY_MINUTES, latest_window

    ML_IMPORT_ERROR: str | None = None
except Exception as _e:  # ml/ missing or broken: say so, never masquerade as a gap
    ML_IMPORT_ERROR = f"{type(_e).__name__}: {_e}"
    HISTORY_MINUTES = 100  # George's features.HISTORY_MIN: what latest_window wants at minimum

    def latest_window(ts_sec, values):  # type: ignore[misc]
        return None


def _load_predict() -> PredictFn | None:
    try:
        from ml.models import predict as p

        p.load_model()  # raises until forecast_v1.json exists (or libomp is missing on macOS)
        return p.predict
    except Exception as e:
        log.warning("forecaster unavailable: %s: %s", type(e).__name__, e)
        return None


@dataclass(frozen=True)
class ForecastResult:
    forecast: Forecast | None
    status: str  # "ok" | "suspended" | "unavailable"
    reason: str | None = None

    def payload(self, at=None) -> dict:
        """The forecast_update payload: the Forecast fields FLAT (predicted_mgdl is
        null when there is no forecast, so a renderer that treats the payload as a
        Forecast draws no line and nothing else breaks) plus forecast (Forecast |
        null), status (ok | suspended | unavailable), reason."""
        flat = self.forecast.model_dump(mode="json") if self.forecast else {
            "timestamp": at.isoformat() if at else None, "predicted_mgdl": None, "horizon_min": 30}
        return {**flat, "forecast": self.forecast.model_dump(mode="json") if self.forecast else None,
                "status": self.status, "reason": self.reason}


class Forecaster:
    def __init__(self, predict: PredictFn | None = None, horizon_min: int = 30) -> None:
        self._predict = predict if predict is not None else _load_predict()
        self.horizon_min = horizon_min
        self.last = ForecastResult(None, "unavailable", "no model loaded")

    @property
    def available(self) -> bool:
        return self._predict is not None

    def _set(self, forecast: Forecast | None, status: str, reason: str | None = None) -> ForecastResult:
        self.last = ForecastResult(forecast, status, reason)
        return self.last

    def reset(self) -> None:
        self.last = ForecastResult(None, "unavailable" if self._predict is None else "suspended", "no forecast yet")

    def forecast(self, history: list[Reading], latest: Reading | None = None) -> ForecastResult:
        """history: readings oldest first covering at least the last 75 min.
        latest: the FEED's latest reading (get_latest), whose is_stale is the
        staleness verdict; history rows never carry it (base.py)."""
        if ML_IMPORT_ERROR:
            return self._set(None, "unavailable", f"ml package not importable ({ML_IMPORT_ERROR})")
        if self._predict is None:
            return self._set(None, "unavailable", "no model loaded")
        if latest is not None and latest.is_stale:
            return self._set(None, "suspended", "latest reading is stale")
        if not history:
            return self._set(None, "suspended", "no readings")
        newest = history[-1]
        if newest.is_stale or (latest is not None and newest.timestamp != latest.timestamp):
            return self._set(None, "suspended", "latest reading is stale")
        ts = np.array([r.timestamp.timestamp() for r in history], dtype=float)
        mgdl = np.array([r.glucose_mgdl for r in history], dtype=float)
        window = latest_window(ts, mgdl)
        if window is None:
            return self._set(None, "suspended", "gap in the last hour")
        hour = newest.timestamp.hour + newest.timestamp.minute / 60
        try:
            value = float(self._predict(window, hour))
        except Exception as e:  # a model error never reaches the alarm path
            return self._set(None, "suspended", f"model error: {type(e).__name__}")
        if not np.isfinite(value):
            return self._set(None, "suspended", "model returned a non-finite value")
        return self._set(Forecast(timestamp=newest.timestamp, predicted_mgdl=value, horizon_min=self.horizon_min), "ok")
