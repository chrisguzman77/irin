import numpy as np

from ml.evaluate import linear_trend, persistence, weighted_roc
from ml.events import forecasts, replay_events, summarize
from ml.models.features import FEATURE_NAMES, WINDOW_LEN, features

T0 = np.datetime64("2020-01-01T23:00:00")        # inside the night window


def times(minutes):
    return T0 + np.asarray(minutes, dtype=np.int64) * np.timedelta64(60, "s")


def grid(n, start=0):
    return [start + 5 * i for i in range(n)]


def run(values, minutes=None, forecast=linear_trend, threshold=70.0):
    x = np.asarray(values, dtype=float)
    ts = times(grid(len(x)) if minutes is None else minutes)
    return replay_events(ts, x, forecasts(ts, x, forecast), threshold=threshold)


def fall_through_70(n_flat=15, start=130.0, slope=2.0, n_after=10):
    # flat, then a steady fall of `slope` mg/dL per 5 min to well below 70
    x = [start] * n_flat
    v = start
    while v > 60:
        v -= slope
        x.append(v)
    return x + [v] * n_after


def test_steady_fall_detected_ahead_by_linear_trend():
    rep = run(fall_through_70())
    assert len(rep["lows"]) == 1
    low = rep["lows"][0]
    assert low["outcome"] == "detected" and low["forecastable"]
    # 2 mg/dL per 5 min -> forecast 12 below current; warns ~ when current < 82
    assert low["lead_min"] >= 25
    assert rep["warnings"][0]["end"] == "escalated"


def test_persistence_never_detects_a_crossing_at_70():
    rep = run(fall_through_70(), forecast=persistence)
    assert rep["lows"][0]["outcome"] == "missed"
    assert rep["warnings"] == []


def test_single_forecast_below_does_not_warn():
    # one reading drop makes exactly one linear forecast dip below 70, then flat recovers
    x = [100.0] * 20 + [85.0] + [100.0] * 20
    rep = run(x, forecast=lambda X: np.where(X[:, 0] == 85.0, 60.0, 100.0))
    assert rep["warnings"] == []


def test_dip_to_72_is_a_false_alarm_at_75():
    x = fall_through_70(start=110.0, slope=2.0)
    x = [v for v in x if v >= 72] + [72.0] * 3 + [80 + 2 * i for i in range(15)]
    rep = run(x, threshold=75.0)
    assert rep["lows"] == []
    assert len(rep["warnings"]) >= 1 and all(w["end"] == "cleared" for w in rep["warnings"])
    s = summarize(rep)
    assert s["night"]["false_alarms"] == len(rep["warnings"])


def test_no_warning_starts_during_actual_low():
    x = fall_through_70() + [55.0 - i for i in range(10)]           # stays low and keeps falling
    rep = run(x)
    assert len(rep["warnings"]) == 1                                 # only the one before the crossing
    assert all(w["time"] < rep["lows"][0]["time"] for w in rep["warnings"])


def test_single_reading_under_70_is_a_low_and_new_event_needs_two_above():
    base = [100.0] * 15
    # 69 (low #1), 71 once, 69 again -> same event; then 71, 71, 69 -> new event
    x = base + [69.0, 71.0, 69.0, 71.0, 71.0, 69.0] + [100.0] * 5
    rep = run(x, forecast=persistence)
    assert len(rep["lows"]) == 2


def test_gap_breaks_the_consecutive_count():
    # the two below-threshold forecasts are on either side of a missing reading
    mins = grid(20) + grid(20, start=5 * 21)                         # one missing slot at minute 100
    x = [100.0] * 40
    rep = run(x, minutes=mins, forecast=lambda X: np.full(len(X), 60.0))
    # the window after the hole is invalid for 12 readings, so warnings start only
    # once two valid forecasts in a row exist on each side
    starts = [int((w["time"] - T0).astype(np.int64) // 60) for w in rep["warnings"]]
    assert starts[0] == 5 * 13                                        # readings 12 and 13 (first two valid)
    preds = forecasts(times(mins), np.asarray(x), lambda X: np.full(len(X), 60.0))
    assert np.isnan(preds[20:32]).all()                              # no forecast across the hole


def test_crossing_right_after_gap_is_unforecastable():
    mins = grid(20) + grid(10, start=5 * 19 + 60)                    # 60-min gap
    x = [120.0] * 20 + [65.0] * 10
    rep = run(x, minutes=mins)
    assert len(rep["lows"]) == 1
    low = rep["lows"][0]
    assert not low["forecastable"] and low["outcome"] == "missed"
    s = summarize(rep)
    assert s["all"]["detection"] == 0.0 and s["all"]["detection_forecastable"] is None


def test_weighted_roc_weights_newest_most():
    # rates newest-first: -3, -1, -1 mg/dL per min -> (3*-3 + 2*-1 + -1)/6 = -2
    w = np.array([150.0] * 9 + [150.0, 145.0, 140.0, 125.0])
    X = features(w, 23.0).reshape(1, -1)
    assert len(FEATURE_NAMES) == X.shape[1] and len(w) == WINDOW_LEN
    assert weighted_roc(X)[0] == 125.0 - 60.0
    assert abs(linear_trend(X)[0] - 75.0) < 1e-9
