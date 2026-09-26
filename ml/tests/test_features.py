import numpy as np
import pytest

from ml.models.features import FEATURE_NAMES, WINDOW_LEN, features, window_ok

# 13 readings, oldest first: 100 at t-60 falling to 76 at t.
WIN = np.array([100, 98, 96, 95, 94, 92, 90, 88, 86, 84, 81, 79, 76], dtype=float)


def f(name, row):
    return row[FEATURE_NAMES.index(name)]


def test_hand_computed_values():
    r = features(WIN, hour=3.5)
    assert r.shape == (len(FEATURE_NAMES),)
    assert f("current", r) == 76
    assert [f(n, r) for n in ("lag5", "lag10", "lag15", "lag30", "lag60")] == [79, 81, 84, 90, 100]
    assert f("roc5", r) == pytest.approx(-3 / 5)
    assert f("roc15", r) == pytest.approx(-8 / 15)
    assert f("roc30", r) == pytest.approx(-14 / 30)
    assert f("accel", r) == pytest.approx(-3 / 5 - (84 - 86) / 5)   # rate now - rate 15 min ago
    assert f("roll_mean", r) == pytest.approx(WIN.mean())
    assert f("roll_std", r) == pytest.approx(WIN.std())
    assert f("roll_min", r) == 76
    assert f("hour_sin", r) == pytest.approx(np.sin(2 * np.pi * 3.5 / 24))
    assert f("night_flag", r) == 1.0


@pytest.mark.parametrize("hour,night", [(21.99, 0), (22.0, 1), (0.0, 1), (6.99, 1), (7.0, 0), (12.0, 0)])
def test_night_window_wraps_midnight(hour, night):
    assert f("night_flag", features(WIN, hour)) == night


def test_batch_equals_single():
    rng = np.random.default_rng(0)
    batch = rng.uniform(40, 300, size=(50, WINDOW_LEN))
    hours = rng.uniform(0, 24, size=50)
    out = features(batch, hours)
    assert out.shape == (50, len(FEATURE_NAMES))
    for i in range(50):
        np.testing.assert_array_equal(out[i], features(batch[i], hours[i]))


def test_wrong_length_rejected():
    with pytest.raises(ValueError):
        features(WIN[:-1], 0.0)


def test_window_ok_full_grid_with_clock_jitter():
    ts = np.arange(WINDOW_LEN) * 300.0 + np.array([0, 4, -3, 7, 2, 0, -5, 9, 1, 3, -2, 6, 0])
    assert window_ok(ts) is True


def test_window_ok_rejects_one_missing_slot():
    ts = np.arange(WINDOW_LEN + 1) * 300.0
    ts = np.delete(ts, 6)                     # 10-min hole in the middle
    assert window_ok(ts) is False


def test_window_ok_rejects_a_gap_and_a_duplicate():
    ts = np.arange(WINDOW_LEN) * 300.0
    gap = ts.copy(); gap[7:] += 31 * 60
    dup = ts.copy(); dup[5] = dup[4] + 60
    assert window_ok(np.vstack([gap, dup, ts])).tolist() == [False, False, True]
