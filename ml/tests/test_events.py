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
    # a stub forecaster dips below 70 on exactly one reading, then is back above
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
    # glucose bottomed at 72 while the warning was on: a near miss, not a far false alarm
    assert all(w["nadir"] == 72.0 for w in rep["warnings"])
    assert s["night"]["false_near"] == s["night"]["false_alarms"] and s["night"]["false_far"] == 0


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


def test_dropped_reading_keeps_forecasting():
    # one missing slot at minute 100: every reading still gets a forecast
    mins = grid(20) + grid(20, start=5 * 21)
    x = np.array([100.0] * 40)
    preds = forecasts(times(mins), x, linear_trend)
    assert np.isnan(preds[:12]).all()                                # the stretch's warm-up hour
    assert not np.isnan(preds[12:]).any()                            # straight through the dropped reading


def test_gap_breaks_the_consecutive_count():
    # a 35-min interval is a gap: new stretch, no forecast for its first hour,
    # and one below-threshold forecast on each side never adds up to a warning
    mins = grid(14) + grid(14, start=5 * 13 + 35)
    x = np.array([100.0] * 28)
    preds = forecasts(times(mins), x, lambda X: np.full(len(X), 60.0))
    assert np.isnan(preds[:12]).all() and not np.isnan(preds[12:14]).any()
    assert np.isnan(preds[14:26]).all() and not np.isnan(preds[26:]).any()
    only_edges = np.full(28, np.nan)
    only_edges[13], only_edges[26] = 60.0, 60.0                     # last before the gap, first after
    rep = replay_events(times(mins), x, only_edges)
    assert rep["warnings"] == []


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


def below_when(limit):
    """Stub forecaster: predicts 60 when the current value is <= limit, else 100."""
    return lambda X: np.where(X[:, 0] <= limit, 60.0, 100.0)


def test_warning_under_10_min_before_crossing_is_late():
    # warns on the 2nd reading <= 74 (the 72), crossing is the next reading: lead 5 min
    x = [100.0] * 15 + [76.0, 74.0, 72.0, 68.0] + [68.0] * 3
    rep = run(x, forecast=below_when(74))
    low = rep["lows"][0]
    assert low["outcome"] == "late" and low["lead_min"] == 5.0
    s = summarize(rep)["all"]
    assert s["late"] == 1 and s["detected"] == 0 and s["median_lead_min"] is None


def test_lead_cap_60_min_is_detected_61_plus_is_long():
    # warning starts on the 2nd reading of the 80 run; crossing follows the run
    for n80, outcome in ((13, "detected"), (14, "long")):
        x = [100.0] * 15 + [80.0] * n80 + [65.0] * 3
        rep = run(x, forecast=below_when(80))
        low = rep["lows"][0]
        assert low["lead_min"] == 5 * (n80 - 1) and low["outcome"] == outcome
    s = summarize(rep)["all"]                                          # the 14-reading (65 min) case
    assert s["long"] == 1 and s["detected"] == 0 and s["detection"] == 0.0
    assert s["median_lead_min"] is None                               # long leads never enter the stats


def test_far_false_alarm_when_glucose_never_came_near():
    x = [150.0] * 30
    rep = run(x, forecast=lambda X: np.where(np.arange(len(X)) < 5, 60.0, 100.0))
    assert len(rep["warnings"]) == 1 and rep["warnings"][0]["end"] == "cleared"
    s = summarize(rep)["all"]
    assert s["false_far"] == 1 and s["false_near"] == 0


def test_warning_still_on_at_end_of_data_is_open_not_false():
    x = [100.0] * 20
    rep = run(x, forecast=lambda X: np.full(len(X), 60.0))
    assert len(rep["warnings"]) == 1 and rep["warnings"][0]["end"] == "open"
    s = summarize(rep)["all"]
    assert s["warnings"] == 1 and s["false_alarms"] == 0


def test_summarize_counts_only_the_range():
    day = 24 * 60
    mins = grid(30) + grid(30, start=day)                             # two nights, one day apart
    x = [100.0] * 20 + [65.0] * 10 + [100.0] * 20 + [65.0] * 10
    rep = run(x, minutes=mins, forecast=persistence)
    assert len(rep["lows"]) == 2
    assert summarize(rep)["all"]["lows"] == 2
    split = T0 + np.timedelta64(day * 60, "s")
    assert summarize(rep, start=split)["all"]["lows"] == 1
    assert summarize(rep, end=split)["all"]["lows"] == 1
    assert summarize(rep, start=split)["days"] == 30 / 288               # coverage counts only in-range readings


def test_forecastable_window_is_40_to_10_min_inclusive():
    x = np.array([100.0] * 20 + [65.0] * 5)                            # crossing at reading 20 = minute 100
    ts = times(grid(len(x)))

    def forecastable(at):                                              # readings that have a forecast
        preds = np.full(len(x), np.nan)
        preds[list(at)] = 100.0
        return replay_events(ts, x, preds)["lows"][0]["forecastable"]

    assert forecastable([12, 18])            # minute 60 (= -40) and 90 (= -10): both ends count
    assert not forecastable([11, 18])        # -45 is outside
    assert not forecastable([12, 19])        # -5 is outside
    assert not forecastable([18])            # one forecast is not enough
