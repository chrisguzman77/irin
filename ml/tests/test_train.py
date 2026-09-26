import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ml.train import PURGE, rows_before


def test_rows_before_purges_the_longest_label_horizon():
    t = pd.date_range("2020-01-01", periods=200, freq="5min")
    rows = pd.DataFrame({"timestamp": t})
    start = t[100]
    kept = rows_before(rows, start)
    # labels land up to 32.3 min after t: every kept row's latest possible label is before start
    assert (kept.timestamp + pd.Timedelta(minutes=32.3) < start).all()
    assert PURGE >= pd.Timedelta(minutes=32.5)
    assert len(kept) == 100 - 7                              # t[93] + 35 min = t[100], excluded


def test_shipped_model_reproduces_its_check_value():
    pytest.importorskip("xgboost")
    check_path = Path(__file__).resolve().parents[1] / "models" / "forecast_v1_check.json"
    if not check_path.exists():
        pytest.skip("forecast_v1 not trained")
    from ml.models import predict

    check = json.loads(check_path.read_text())
    window = [np.nan if v is None else v for v in check["window"]]
    assert abs(predict.predict(window, check["hour"]) - check["expected"]) < 1e-3
