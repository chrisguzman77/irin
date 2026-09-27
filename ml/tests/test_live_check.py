"""ml/live_check.py: its local recomputation is the device's exact forecast
path, and its calibration verdict reads the right way round. SYNTHETIC data."""

from datetime import datetime, timedelta

import numpy as np
import pytest

pytest.importorskip("xgboost")

from ml.live_check import Tally, local_forecast  # noqa: E402
from ml.models.features import latest_window  # noqa: E402
from ml.models.predict import predict  # noqa: E402


def history(values, start=datetime(2020, 1, 1, 1, 0)):
    return [{"timestamp": (start + timedelta(minutes=5 * k)).isoformat(), "glucose_mgdl": float(v)}
            for k, v in enumerate(values)]


def test_local_forecast_is_forecast_py_path_and_uses_only_readings_up_to_then():
    vals = list(np.linspace(180, 90, 30))
    h = history(vals)
    at = datetime.fromisoformat(h[24]["timestamp"])
    ts = np.array([datetime.fromisoformat(r["timestamp"]).timestamp() for r in h[:25]])
    expected = predict(latest_window(ts, np.array(vals[:25])), at.hour + at.minute / 60)
    assert local_forecast(h, at) == pytest.approx(expected, abs=1e-9)   # later readings are ignored
    assert local_forecast(h[:5], datetime.fromisoformat(h[4]["timestamp"])) is None   # under an hour: no forecast


def test_calibration_verdict_direction():
    t = Tally()
    t.status["ok"] = 1
    t.resolved = [(100.0, 120.0)] * 80 + [(100.0, 90.0)] * 20            # 80% above: as trained
    assert "as trained" in t.summary()
    t.resolved = [(100.0, 120.0)] * 99 + [(100.0, 90.0)] * 1             # 99% above
    assert "TOO PESSIMISTIC" in t.summary()
    t.resolved = [(100.0, 120.0)] * 50 + [(100.0, 90.0)] * 50            # 50% above
    assert "TOO OPTIMISTIC" in t.summary()
