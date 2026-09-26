import numpy as np
import pandas as pd

from ml.events import forecasts, replay_events, summarize
from ml.evaluate import linear_trend
from ml.review_lows import load_flags, plot_low, shape

T0 = np.datetime64("2020-01-01T23:00:00")


def times(n):
    return T0 + np.arange(n) * np.timedelta64(300, "s")


def v_shaped():
    # SYNTHETIC: flat, a steep fall, 3 readings under 70, a fast recovery
    return np.array([130.0] * 40 + [110, 90, 72, 62, 58, 64, 85, 110] + [120.0] * 20)


def test_shape_measures_a_steep_v():
    x = v_shaped()
    ts = times(len(x))
    i = int(np.flatnonzero(x < 70)[0])
    s = shape(ts, x, i)
    assert s["nadir"] == 58.0
    assert s["min_below_70"] == 15.0                      # 3 readings under 70
    assert s["fall_15_before"] == (62 - 110) / 15
    assert s["rise_15_after_nadir"] == (110 - 58) / 15


def test_plot_writes_a_file(tmp_path):
    x = v_shaped()
    ts = times(len(x))
    preds = forecasts(ts, x, linear_trend)
    rep = replay_events(ts, x, preds, threshold=85.0)
    out = tmp_path / "low_01.png"
    plot_low(out, ts, x, preds, rep["lows"][0], "synthetic")
    assert out.exists() and out.stat().st_size > 1000


def test_flagged_low_leaves_the_denominator(tmp_path):
    x = np.concatenate([v_shaped(), v_shaped()])
    ts = times(len(x))
    rep = replay_events(ts, x, forecasts(ts, x, linear_trend), threshold=85.0)
    assert len(rep["lows"]) == 2
    first = rep["lows"][0]["time"]
    flags = tmp_path / "review_flags.csv"
    pd.DataFrame({"low_id": [1, 2], "crossing": [str(pd.Timestamp(first)), str(pd.Timestamp(rep["lows"][1]["time"]))],
                  "outcome": ["x", "x"], "flag_artifact": ["Y", ""], "note": ["steep V", ""]}).to_csv(flags, index=False)
    exclude = load_flags(flags)
    assert exclude == {first}
    s = summarize(rep, exclude=exclude)
    assert s["all"]["lows"] == 1 and s["excluded_lows"] == 1
    assert summarize(rep)["all"]["lows"] == 2


def test_missing_flags_file_excludes_nothing(tmp_path):
    assert load_flags(tmp_path / "none.csv") == set()
