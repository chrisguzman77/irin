"""The ONE feature function (george.md "Shared feature code"), imported by
both build_dataset.py (training) and predict.py (the Pi). numpy only, so the
Pi needs no pandas; vectorized over a matrix of windows, so training runs the
exact lines the Pi runs. Never re-implement a feature in two places.

Features (16, raw mg/dL, no normalization): current value; lags at
5/10/15/30/60 min; rate of change over 5/15/30 min (mg/dL per min);
acceleration (the 5-min rate now minus the 5-min rate 15 min ago); rolling
mean/std/min of the window; sin/cos of hour + night-window flag. No
insulin/carb features in v1. The window is 13 readings on a strict 5-min grid
(60 min), oldest first; window_ok() is the eligibility rule that training,
the event replay, and the backend all apply before calling features()."""

from __future__ import annotations

import numpy as np

FEATURE_NAMES: list[str] = [
    "current", "lag5", "lag10", "lag15", "lag30", "lag60",
    "roc5", "roc15", "roc30", "accel",
    "roll_mean", "roll_std", "roll_min",
    "hour_sin", "hour_cos", "night_flag",
]
WINDOW_LEN = 13
SLOT_MIN = 5          # Dexcom reading spacing; a diff rounds to a number of slots
HORIZON_SLOTS = 6     # the label sits 30 min (6 slots) after t


def _hours(hhmm: str) -> float:
    h, m = hhmm.split(":")
    return int(h) + int(m) / 60


def slots_between(dt_seconds: np.ndarray) -> np.ndarray:
    """Number of 5-min grid slots spanned by each interval. Half-up rounding
    (numpy's round is half-to-even), so 7.5 min is 2 slots on every machine."""
    return np.floor(np.asarray(dt_seconds, dtype=float) / (SLOT_MIN * 60) + 0.5).astype(np.int64)


def window_ok(ts_seconds: np.ndarray) -> np.ndarray | bool:
    """ts_seconds: the timestamps (POSIX seconds) of the last WINDOW_LEN
    readings, oldest first, shape (13,) or (N, 13). True only when every
    consecutive pair is exactly one grid slot apart, i.e. the full 60 min is
    present with no missing reading and no gap. Otherwise: no forecast, never
    an interpolated window."""
    ts = np.asarray(ts_seconds, dtype=float)
    single = ts.ndim == 1
    ts = np.atleast_2d(ts)
    if ts.shape[1] != WINDOW_LEN:
        raise ValueError(f"window_ok needs {WINDOW_LEN} timestamps per window, got {ts.shape[1]}")
    ok = (slots_between(np.diff(ts, axis=1)) == 1).all(axis=1)
    return bool(ok[0]) if single else ok


def features(window: np.ndarray, hour: float | np.ndarray,
             night_window: tuple[str, str] = ("22:00", "07:00")) -> np.ndarray:
    """window: readings (mg/dL) oldest first, shape (13,) or (N, 13), already
    passed window_ok. hour: local hour of the newest reading (fractional), a
    scalar or shape (N,). Returns shape (16,) or (N, 16) aligned with
    FEATURE_NAMES. Uses only the window and t's clock time: no future input."""
    w = np.asarray(window, dtype=float)
    single = w.ndim == 1
    w = np.atleast_2d(w)
    if w.shape[1] != WINDOW_LEN:
        raise ValueError(f"features needs {WINDOW_LEN} readings per window, got {w.shape[1]}")
    h = np.broadcast_to(np.asarray(hour, dtype=float), (w.shape[0],))

    now = w[:, -1]
    lag = {m: w[:, -1 - m // SLOT_MIN] for m in (5, 10, 15, 30, 60)}
    roc5 = (now - lag[5]) / 5
    roc15 = (now - lag[15]) / 15
    roc30 = (now - lag[30]) / 30
    roc5_15ago = (lag[15] - w[:, -5]) / 5          # reading 15 min ago minus 20 min ago
    accel = roc5 - roc5_15ago

    start, end = _hours(night_window[0]), _hours(night_window[1])
    if start > end:                                  # window wraps midnight
        night = (h >= start) | (h < end)
    else:
        night = (h >= start) & (h < end)
    angle = 2 * np.pi * h / 24

    out = np.column_stack([
        now, lag[5], lag[10], lag[15], lag[30], lag[60],
        roc5, roc15, roc30, accel,
        w.mean(axis=1), w.std(axis=1), w.min(axis=1),
        np.sin(angle), np.cos(angle), night.astype(float),
    ])
    return out[0] if single else out
