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


def test_one_missing_reading_costs_only_its_own_row_and_its_label_row():
    n = 60
    mins = regular(n)
    hole = 30
    ts, x = series([150.0] * (n - 1), mins[:hole] + mins[hole + 1:])
    rows, s = build(ts, x)
    full, _ = build(*series([150.0] * n, mins))
    # lost: the row AT slot 30 (no reading) and the row whose label is slot 30 (t = slot 24)
    lost = set(full.timestamp) - set(rows.timestamp)
    lost_slots = sorted(int((t - ts[0]).total_seconds() // 300) for t in lost)
    assert lost_slots == [24, 30]
    assert s["stretches"] == 1 and s["no_window_hole"] == 0
    assert s["window_with_missing_slot"] == 12                         # windows ending at slots 31..42
    assert s["nan_cells"] == 0 and s["rows_touching_gap"] == 0 and s["inputs_all_at_or_before_t"]
    # windows ending at 31..42 see slot 30 empty; at t = slot 33 it is the lag15 slot -> NaN
    row = rows[rows.timestamp == ts[0] + pd.Timedelta(minutes=5 * 33)].iloc[0]
    assert np.isnan(row.lag15) and not np.isnan(row.lag10)


def test_six_missing_readings_is_a_gap_and_splits_the_stretch():
    n = 60
    mins = regular(n)
    ts, x = series([150.0] * (n - 6), mins[:30] + mins[36:])         # 35 min between readings
    rows, s = build(ts, x)
    assert s["stretches"] == 2 and s["rows_touching_gap"] == 0


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
    assert s["nan_cells"] == 0 and s["window_with_missing_slot"] > 0   # the pair's slots are NaN, never guessed
