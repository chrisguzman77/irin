"""The ONE feature function (george.md "Shared feature code"), imported by
both build_dataset.py (training) and predict.py (the Pi). numpy only, so the
Pi needs no pandas; vectorized over a matrix of windows, so training runs the
exact lines the Pi runs. Never re-implement a feature in two places.

Features (16, raw mg/dL, no normalization): current value; lags at
5/10/15/30/60 min; rate of change over 5/15/30 min (mg/dL per min);
acceleration (the 5-min rate now minus the 5-min rate 15 min ago); rolling
mean/std/min of the window; sin/cos of hour + night-window flag. No
insulin/carb features in v1.

The window is 13 five-minute SLOTS (60 min), oldest first, ending on the
current reading. A slot with no reading is NaN: never interpolated. A dropped
reading is allowed (George, 2026-09-26); a window is usable when
  - the current reading is present,
  - no run of more than MAX_EMPTY_SLOTS empty slots (6 empty = over 30 min
    without a reading, step 1's gap definition), and
  - the whole window lies inside one gap-free stretch (a gap > 30 min starts
    a new stretch; the first hour after it is warm-up, no forecast).
A lag or rate whose slot is empty is NaN; XGBoost routes missing values
natively. Slots come from grid_slots (cumulative rounded reading spacing),
and slot_windows / latest_window are the ONLY way to build windows, for
training, the event replay, and the Pi alike."""

from __future__ import annotations

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

FEATURE_NAMES: list[str] = [
    "current", "lag5", "lag10", "lag15", "lag30", "lag60",
    "roc5", "roc15", "roc30", "accel",
    "roll_mean", "roll_std", "roll_min",
    "hour_sin", "hour_cos", "night_flag",
]
WINDOW_LEN = 13
SLOT_MIN = 5          # Dexcom reading spacing; a diff rounds to a number of slots
HORIZON_SLOTS = 6     # the label sits 30 min (6 slots) after t
GAP_MIN = 30          # an interval longer than this is a gap (step 1) and splits stretches
MAX_EMPTY_SLOTS = 5   # 5 empty slots in a row = readings 30 min apart, still not a gap
STRETCH_JUMP = 2 * WINDOW_LEN   # slot offset between stretches: no window or label spans two
# How much history latest_window needs to rebuild the training window exactly:
# the reading that fills the oldest slot can sit up to 60 + 2.5 (slot rounding)
# min back, and the reading before it up to 30 min further (a longer interval
# is a gap). 60 + 2.5 + 30 = 92.5; 100 leaves margin for a collision pair.
HISTORY_MIN = 100


def _hours(hhmm: str) -> float:
    h, m = hhmm.split(":")
    return int(h) + int(m) / 60


def slots_between(dt_seconds: np.ndarray) -> np.ndarray:
    """Number of 5-min grid slots spanned by each interval. Half-up rounding
    (numpy's round is half-to-even), so 7.5 min is 2 slots on every machine."""
    return np.floor(np.asarray(dt_seconds, dtype=float) / (SLOT_MIN * 60) + 0.5).astype(np.int64)


def drop_collisions(ts_sec: np.ndarray) -> np.ndarray:
    """Keep-mask removing BOTH readings of any pair less than half a slot apart
    (they cannot share one grid slot, and neither is the right one to keep).
    An EXACT duplicate timestamp (a repeated poll or row) is the same reading,
    not a collision: the first copy is kept, the rest dropped.
    Known, rare skew: a reading is dropped when the NEXT reading arrives within
    2.5 min, so training and the replay drop it after the fact while the Pi,
    at that moment, has already forecast from it."""
    ts_sec = np.asarray(ts_sec, dtype=np.int64)
    keep = np.ones(len(ts_sec), dtype=bool)
    if len(ts_sec) > 1:
        keep[1:] = np.diff(ts_sec) != 0
    while True:
        idx = np.flatnonzero(keep)
        close = slots_between(np.diff(ts_sec[idx])) == 0
        if not close.any():
            return keep
        keep[idx[:-1][close]] = False
        keep[idx[1:][close]] = False


def grid_slots(ts_sec: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """ts_sec sorted ascending (POSIX seconds). Returns (keep, gslot, stretch):
    the collision keep-mask over the input, and for the KEPT readings their
    grid slot and stretch number. Slots are the cumulative rounded spacing, so
    a reading's slot offset from any later reading depends only on the readings
    between them (the Pi's short history gives the same offsets as training's
    full one)."""
    ts_sec = np.asarray(ts_sec, dtype=np.int64)
    keep = drop_collisions(ts_sec)
    t = ts_sec[keep]
    if len(t) == 0:
        return keep, np.zeros(0, np.int64), np.zeros(0, np.int64)
    step = np.diff(t)
    is_gap = step > GAP_MIN * 60
    stretch = np.concatenate([[0], np.cumsum(is_gap)])
    slot_step = np.where(is_gap, STRETCH_JUMP, slots_between(step))
    gslot = np.concatenate([[0], np.cumsum(slot_step)])
    return keep, gslot, stretch


def window_ok(windows: np.ndarray) -> np.ndarray | bool:
    """The NaN-pattern half of the rule on (13,) or (N, 13) slot windows:
    current slot present and no run of more than MAX_EMPTY_SLOTS empty slots.
    (The stretch half is checked by slot_windows, which knows the stretches.)"""
    w = np.asarray(windows, dtype=float)
    single = w.ndim == 1
    w = np.atleast_2d(w)
    if w.shape[1] != WINDOW_LEN:
        raise ValueError(f"window_ok needs {WINDOW_LEN} slots per window, got {w.shape[1]}")
    empty = np.isnan(w)
    run = np.zeros(len(w), np.int64)
    longest = np.zeros(len(w), np.int64)
    for k in range(WINDOW_LEN):
        run = np.where(empty[:, k], run + 1, 0)
        longest = np.maximum(longest, run)
    ok = ~empty[:, -1] & (longest <= MAX_EMPTY_SLOTS)
    return bool(ok[0]) if single else ok


def slot_windows(gslot: np.ndarray, stretch: np.ndarray, values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """One (13,) slot window per reading, ending on it, NaN where a slot has no
    reading. Returns (windows (N, 13), ok (N,)). ok = window_ok AND the window
    starts inside the reading's own stretch (no forecast in a stretch's first
    hour, never across a gap). Uses only readings at or before each one."""
    gslot = np.asarray(gslot, dtype=np.int64)
    x = np.asarray(values, dtype=float)
    n = len(x)
    if n == 0:
        return np.zeros((0, WINDOW_LEN)), np.zeros(0, bool)
    pad = WINDOW_LEN - 1
    grid = np.full(int(gslot[-1]) + 1 + pad, np.nan)
    grid[gslot + pad] = x
    windows = sliding_window_view(grid, WINDOW_LEN)[gslot]          # window i covers slots gslot[i]-12 .. gslot[i]
    stretch = np.asarray(stretch)
    first = np.full(int(stretch[-1]) + 1, -1, np.int64)
    first[stretch[::-1]] = gslot[::-1]                                # first slot of each stretch
    in_stretch = gslot - pad >= first[stretch]
    return windows, window_ok(windows) & in_stretch


def latest_window(ts_sec: np.ndarray, values: np.ndarray) -> np.ndarray | None:
    """The Pi's entry point: recent readings (sorted, POSIX seconds; pass at
    least the last HISTORY_MIN = 100 min) -> the (13,) slot window ending on the newest
    reading, or None when there is no forecast (window not usable). The same
    grid_slots + slot_windows lines training runs. Staleness of the newest
    reading is the backend's rule, not this function's."""
    ts_sec = np.asarray(ts_sec, dtype=np.int64)
    values = np.asarray(values, dtype=float)
    n = len(ts_sec)
    while n > 1 and ts_sec[n - 1] == ts_sec[n - 2]:          # a repeated newest reading is the same reading
        n -= 1
    ts_sec, values = ts_sec[:n], values[:n]
    keep, gslot, stretch = grid_slots(ts_sec)
    if not keep[-1:].all() or len(gslot) == 0:
        return None
    windows, ok = slot_windows(gslot, stretch, values[keep])
    return windows[-1] if ok[-1] else None


def features(window: np.ndarray, hour: float | np.ndarray,
             night_window: tuple[str, str] = ("22:00", "07:00")) -> np.ndarray:
    """window: slot values (mg/dL) oldest first, shape (13,) or (N, 13), NaN
    for an empty slot, already passed window_ok. hour: local hour of the
    newest reading (fractional), a scalar or shape (N,). Returns shape (16,) or
    (N, 16) aligned with FEATURE_NAMES; a lag or rate that needs an empty slot
    is NaN. Uses only the window and t's clock time: no future input."""
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
        np.nanmean(w, axis=1), np.nanstd(w, axis=1), np.nanmin(w, axis=1),
        np.sin(angle), np.cos(angle), night.astype(float),
    ])
    return out[0] if single else out
