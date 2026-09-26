import pandas as pd

from ml.clean_clarity import find_gaps, spike_mask


def series(values, minutes=None):
    minutes = minutes if minutes is not None else [5 * i for i in range(len(values))]
    ts = pd.Series(pd.Timestamp("2020-01-01") + pd.to_timedelta(minutes, unit="m"))
    return ts, pd.Series(values, dtype=float)


def test_opposite_sign_spike_is_dropped():
    ts, x = series([100, 100, 150, 100, 100])
    assert spike_mask(ts, x).tolist() == [False, False, True, False, False]


def test_real_crash_same_sign_never_fires():
    ts, x = series([200, 165, 130, 95, 60])
    assert not spike_mask(ts, x).any()


def test_exactly_30_is_not_a_spike():
    ts, x = series([100, 130, 100])
    assert not spike_mask(ts, x).any()


def test_no_spike_across_a_gap():
    ts, x = series([100, 150, 100], minutes=[0, 60, 65])
    assert not spike_mask(ts, x).any()


def test_gap_is_strictly_over_30_min():
    ts, _ = series([0, 0, 0], minutes=[0, 30, 61])
    gaps = find_gaps(ts)
    assert len(gaps) == 1 and gaps.length.iloc[0] == pd.Timedelta(minutes=31)
