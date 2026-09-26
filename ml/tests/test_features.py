import numpy as np
import pytest

from ml.models.features import (FEATURE_NAMES, WINDOW_LEN, features, grid_slots, latest_window, slot_windows,
                                window_ok)

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


def windows_for(minutes, values=None):
    ts = np.asarray(minutes, dtype=float) * 60
    x = np.full(len(ts), 120.0) if values is None else np.asarray(values, dtype=float)
    keep, gslot, stretch = grid_slots(ts.astype(np.int64))
    return slot_windows(gslot, stretch, x[keep])


def test_full_grid_with_clock_jitter_is_ok_after_warmup():
    jitter = np.array([0, 4, -3, 7, 2, 0, -5, 9, 1, 3, -2, 6, 0, 5, -4]) / 60
    w, ok = windows_for(np.arange(15) * 5 + jitter)
    assert ok.tolist() == [False] * 12 + [True] * 3                  # first hour of a stretch: no forecast
    assert not np.isnan(w[ok]).any()


def test_dropped_reading_is_a_nan_slot_not_a_rejection():
    mins = [5 * i for i in range(20) if i != 16]                     # reading at minute 80 dropped
    w, ok = windows_for(mins)
    assert ok[12:].all()
    last = w[-1]                                                      # ends at minute 95: slot 80 is lag15
    assert np.isnan(last).sum() == 1 and np.isnan(last[-4])
    r = features(last, 3.0)
    for name in ("lag15", "roc15", "accel"):
        assert np.isnan(f(name, r))
    for name in ("current", "lag5", "lag10", "lag30", "lag60", "roc5", "roc30", "roll_mean", "roll_std", "roll_min"):
        assert not np.isnan(f(name, r))
    assert f("roll_mean", r) == 120.0


@pytest.mark.parametrize("run,ok", [(1, True), (5, True), (6, False)])
def test_window_ok_allows_up_to_five_empty_slots_in_a_row(run, ok):
    w = np.full(WINDOW_LEN, 100.0)
    w[3:3 + run] = np.nan
    assert window_ok(w) is ok


def test_window_ok_needs_the_current_reading_and_counts_leading_runs():
    w = np.full(WINDOW_LEN, 100.0)
    cur = w.copy(); cur[-1] = np.nan
    lead5 = w.copy(); lead5[:5] = np.nan
    lead6 = w.copy(); lead6[:6] = np.nan
    assert window_ok(np.vstack([cur, lead5, lead6, w])).tolist() == [False, True, False, True]


def test_readings_30_min_apart_ok_31_min_is_a_gap():
    before = [5 * i for i in range(14)]                              # 0..65
    w30, ok30 = windows_for(before + [65 + 30 + 5 * i for i in range(3)])
    assert ok30[-3:].all()                                            # 5 empty slots, same stretch
    w31, ok31 = windows_for(before + [65 + 31 + 5 * i for i in range(14)])
    assert not ok31[14:26].any() and ok31[26:].all()                  # new stretch: warm-up hour again


def test_collision_pair_leaves_an_empty_slot():
    mins = [5 * i for i in range(20)]
    mins.insert(18, mins[17] + 1)                                     # a reading 1 min after another
    ts = np.asarray(mins, dtype=np.int64) * 60
    keep, gslot, stretch = grid_slots(ts)
    assert (~keep).sum() == 2                                         # both dropped, neither guessed
    w, ok = slot_windows(gslot, stretch, np.full(keep.sum(), 120.0))
    assert ok[-1] and np.isnan(w[-1]).sum() == 1


def test_latest_window_matches_training_windows_on_every_prefix():
    # the Pi's entry point must build the same window training built, reading by reading
    rng = np.random.default_rng(3)
    mins = np.cumsum(rng.choice([5, 5, 5, 5, 10, 15, 40], size=300)) + rng.uniform(-0.2, 0.2, 300)
    ts = (mins * 60).astype(np.int64)
    x = rng.uniform(50, 300, 300)
    keep, gslot, stretch = grid_slots(ts)
    assert keep.all()
    w, ok = slot_windows(gslot, stretch, x)
    assert ok.sum() > 50 and (~ok).sum() > 50
    for i in range(len(ts)):
        got = latest_window(ts[max(0, i - 30):i + 1], x[max(0, i - 30):i + 1])
        if ok[i]:
            np.testing.assert_array_equal(got, w[i])
        else:
            assert got is None


def test_latest_window_with_exactly_history_min_matches_training():
    # the documented contract, not a generous prefix: readings dropped 55-90 min
    # back (the reviewer's case) must not change what the Pi builds
    from ml.models.features import HISTORY_MIN

    rng = np.random.default_rng(7)
    steps = rng.choice([5, 5, 5, 5, 5, 10, 15, 20, 30, 45], size=600)
    mins = np.cumsum(steps) + rng.uniform(-0.3, 0.3, 600)
    ts = (mins * 60).astype(np.int64)
    x = rng.uniform(50, 300, 600)
    keep, gslot, stretch = grid_slots(ts)
    assert keep.all()
    w, ok = slot_windows(gslot, stretch, x)
    assert ok.sum() > 100 and (np.isnan(w[ok]).any(axis=1)).sum() > 20
    for i in range(len(ts)):
        lo = np.searchsorted(ts, ts[i] - HISTORY_MIN * 60)
        got = latest_window(ts[lo:i + 1], x[lo:i + 1])
        if ok[i]:
            np.testing.assert_array_equal(got, w[i])
        else:
            assert got is None


def test_repeated_newest_reading_still_forecasts():
    mins = [5 * i for i in range(20)]
    ts = np.asarray(mins + [mins[-1]], dtype=np.int64) * 60          # the last poll returned the same reading twice
    x = np.full(len(ts), 120.0)
    got = latest_window(ts, x)
    assert got is not None and not np.isnan(got).any()
