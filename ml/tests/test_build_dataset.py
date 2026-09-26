import numpy as np
import pandas as pd

from ml.build_dataset import build
from ml.models.features import FEATURE_NAMES


def series(values, minutes):
    ts = pd.Series(pd.Timestamp("2020-01-01") + pd.to_timedelta(minutes, unit="m"))
    return ts, pd.Series(values, dtype=float)


def regular(n, start=0):
    return [start + 5 * i for i in range(n)]


def test_one_clean_stretch_row_count_and_label():
    n = 40
    ts, x = series(np.arange(n) * 2.0 + 100, regular(n))
    rows, s = build(ts, x)
    # first row ends on reading 12, last row needs reading i+6 -> 40 - 12 - 6 rows
    assert len(rows) == n - 12 - 6 == s["rows"]
    assert (rows.y_delta == 12.0).all()                      # 6 slots x 2 mg/dL
    assert rows.timestamp.iloc[0] == ts[12]
    assert s["nan_cells"] == 0 and s["rows_touching_gap"] == 0
    assert s["label_ahead_min"] == (30.0, 30.0)
    assert s["inputs_all_at_or_before_t"]


def test_no_row_across_a_31_min_gap():
    ts, x = series([120.0] * 60, regular(30) + regular(30, start=5 * 29 + 31))
    rows, s = build(ts, x)
    assert s["stretches"] == 2
    assert len(rows) == 2 * (30 - 12 - 6)
    assert s["rows_touching_gap"] == 0
    # no row's window or label spans the gap
    gap_end = ts[30]
    t = rows.timestamp
    assert not ((t < gap_end) & (t + pd.Timedelta(minutes=30) >= gap_end)).any()
    assert not ((t >= gap_end) & (t - pd.Timedelta(minutes=60) < gap_end)).any()


def test_one_missing_reading_removes_exactly_the_rows_that_need_it():
    n = 60
    mins = regular(n)
    hole = 30
    ts, x = series([150.0] * (n - 1), mins[:hole] + mins[hole + 1:])
    rows, s = build(ts, x)
    full, _ = build(*series([150.0] * n, mins))
    # rows needing slot 30: windows ending at 30..42 (13) plus labels at 30 (t = slot 24)
    lost = set(full.timestamp) - set(rows.timestamp)
    lost_slots = sorted(int((t - ts[0]).total_seconds() // 300) for t in lost)
    assert lost_slots == [24] + list(range(30, 43))
    assert s["stretches"] == 1 and s["no_window_hole"] == 12


def test_future_values_never_change_features():
    n = 50
    rng = np.random.default_rng(1)
    vals = rng.uniform(60, 250, n)
    ts, x = series(vals, regular(n))
    rows, _ = build(ts, x)
    k = 20                                                   # change everything after reading k
    x2 = x.copy()
    x2[k + 1:] = 400.0
    rows2, _ = build(ts, x2)
    upto = rows.timestamp <= ts[k]
    np.testing.assert_array_equal(rows.loc[upto, FEATURE_NAMES].to_numpy(),
                                  rows2.loc[upto, FEATURE_NAMES].to_numpy())


def test_collision_pair_is_dropped_not_guessed():
    mins = regular(40)
    mins.insert(20, mins[19] + 1)                            # a reading 1 min after another
    ts, x = series([100.0] * 41, mins)
    rows, s = build(ts, x)
    assert s["collision_dropped"] == 2
    assert s["nan_cells"] == 0
