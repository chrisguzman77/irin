"""george.md step 2: one row per reading only when the full 60-min feature history AND the t+30 label sit inside one gap-free stretch; label = glucose(t+30) - glucose(t) (the DELTA); features via models/features.py ONLY; no future information; prints row count and NaN check.

Strict 5-min grid: a row needs all 13 window slots filled (features.window_ok, the same rule the event replay and the Pi apply) and the slot 30 min later filled, in the same stretch. A missing reading is never interpolated; it removes the rows around it. Reads ml/data/clean.csv, writes ml/data/dataset.csv (gitignored). Prints summaries only."""

import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from ml.models.features import FEATURE_NAMES, HORIZON_SLOTS, WINDOW_LEN, features, slots_between, window_ok

GAP_MIN = 30                # same gap definition as clean_clarity.py
STRETCH_JUMP = 10**6        # slot offset between stretches, so a label can never land in the next stretch


def drop_collisions(ts_sec: np.ndarray) -> np.ndarray:
    """Keep-mask removing BOTH readings of any pair less than half a slot apart
    (they cannot share one grid slot, and neither is the right one to keep)."""
    keep = np.ones(len(ts_sec), dtype=bool)
    while True:
        idx = np.flatnonzero(keep)
        close = slots_between(np.diff(ts_sec[idx])) == 0
        if not close.any():
            return keep
        keep[idx[:-1][close]] = False
        keep[idx[1:][close]] = False


def build(ts: pd.Series, mgdl: pd.Series) -> tuple[pd.DataFrame, dict]:
    """ts sorted ascending, naive local time. Returns (rows, stats)."""
    ts = pd.Series(pd.to_datetime(ts)).reset_index(drop=True)
    ts_sec_all = ts.to_numpy("datetime64[s]").astype(np.int64)
    keep = drop_collisions(ts_sec_all)
    ts, ts_sec = ts[keep].reset_index(drop=True), ts_sec_all[keep]
    x = np.asarray(mgdl, dtype=float)[keep]
    n = len(x)
    stats = {"readings": len(keep), "collision_dropped": int((~keep).sum())}

    step = np.diff(ts_sec)
    is_gap = step > GAP_MIN * 60
    stretch = np.concatenate([[0], np.cumsum(is_gap)])
    slot_step = np.where(is_gap, STRETCH_JUMP, slots_between(step))
    gslot = np.concatenate([[0], np.cumsum(slot_step)])
    stats["stretches"] = int(stretch[-1] + 1) if n else 0

    if n < WINDOW_LEN:
        return pd.DataFrame(columns=["timestamp", "stretch", *FEATURE_NAMES, "y_delta", "y_abs"]), stats

    t_idx = np.arange(WINDOW_LEN - 1, n)                       # the reading each window ends on
    w_ts = sliding_window_view(ts_sec, WINDOW_LEN)
    w_x = sliding_window_view(x, WINDOW_LEN)
    ok = window_ok(w_ts)

    target = gslot[t_idx] + HORIZON_SLOTS
    j = np.searchsorted(gslot, target)
    j_safe = np.minimum(j, n - 1)
    has_label = (j < n) & (gslot[j_safe] == target)
    kept = ok & has_label

    # Why readings have no window: the stretch's first hour (warm-up after a
    # sensor change or gap), or a missing reading inside a stretch.
    warm = np.concatenate([np.ones(WINDOW_LEN - 1, bool), stretch[t_idx - (WINDOW_LEN - 1)] != stretch[t_idx]])
    no_window = np.concatenate([np.ones(WINDOW_LEN - 1, bool), ~ok])
    stats.update(
        candidates=n,
        no_window_warmup=int((no_window & warm).sum()),
        no_window_hole=int((no_window & ~warm).sum()),
        window_ok_no_label=int((ok & ~has_label).sum()),
        rows=int(kept.sum()),
    )

    ti, ji = t_idx[kept], j[kept]
    hour = (ts.dt.hour + ts.dt.minute / 60 + ts.dt.second / 3600).to_numpy()[ti]
    X = features(w_x[kept], hour)
    rows = pd.DataFrame(X, columns=FEATURE_NAMES)
    rows.insert(0, "timestamp", ts.to_numpy()[ti])
    rows.insert(1, "stretch", stretch[ti])
    rows["y_delta"] = x[ji] - x[ti]
    rows["y_abs"] = x[ji]

    # Independent checks on what was built (printed, and asserted in tests).
    t, lab = ts_sec[ti], ts_sec[ji]
    # A row crosses a gap iff some gap starts at a reading from its oldest
    # window reading up to (not including) its label reading. Real timestamps,
    # not nominal t-60/t+30: a window may start on a gap's far edge.
    first = ts_sec[ti - (WINDOW_LEN - 1)]
    gap_start = ts_sec[:-1][is_gap]
    crossings = np.searchsorted(gap_start, lab, "left") - np.searchsorted(gap_start, first, "left")
    span = (t - first) / 60
    stats.update(
        nan_cells=int(rows[FEATURE_NAMES + ["y_delta"]].isna().sum().sum()),
        rows_touching_gap=int((crossings > 0).sum()),
        window_span_min=(float(span.min()), float(span.max())) if len(span) else (0.0, 0.0),
        label_ahead_min=(float(((lab - t) / 60).min()), float(((lab - t) / 60).max())) if len(t) else (0.0, 0.0),
        inputs_all_at_or_before_t=bool((w_ts[kept].max(axis=1) == t).all()),
    )
    return rows, stats


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", default="ml/data")
    args = p.parse_args()
    data = Path(args.data)
    t0 = time.perf_counter()

    clean = pd.read_csv(data / "clean.csv", parse_dates=["timestamp"]).sort_values("timestamp", ignore_index=True)
    rows, s = build(clean.timestamp, clean.mgdl)

    total = s["readings"]
    print(f"clean readings: {total}   stretches (split at gaps > {GAP_MIN} min): {s['stretches']}")
    print(f"collision pairs removed (readings < 2.5 min apart): {s['collision_dropped']} readings")
    print("\nREADINGS WITH NO VALID 60-MIN WINDOW (= no forecast on the device):")
    print(f"  first hour of a stretch (warm-up after gap/sensor change): {s['no_window_warmup']}"
          f"  ({100 * s['no_window_warmup'] / total:.2f}% of readings)")
    print(f"  missing reading inside a stretch:                         {s['no_window_hole']}"
          f"  ({100 * s['no_window_hole'] / total:.2f}% of readings)")
    print(f"window ok but no reading exactly 30 min later: {s['window_ok_no_label']}")
    print(f"\nROWS: {s['rows']}  ({100 * s['rows'] / total:.1f}% of readings)")
    print(f"features: {len(FEATURE_NAMES)}  {', '.join(FEATURE_NAMES)}")

    print("\nCHECKS")
    print(f"  NaN cells in features + label:            {s['nan_cells']}")
    print(f"  rows whose window..label crosses a gap:      {s['rows_touching_gap']}")
    print(f"  window span t-60 -> t (min):                {s['window_span_min'][0]:.2f} .. {s['window_span_min'][1]:.2f}")
    print(f"  label ahead of t (min):                     {s['label_ahead_min'][0]:.2f} .. {s['label_ahead_min'][1]:.2f}")
    print(f"  every window input timestamp <= t:          {s['inputs_all_at_or_before_t']}")

    y = rows.y_delta
    print("\nLABEL y_delta (mg/dL over 30 min):")
    print(f"  mean {y.mean():.1f}  sd {y.std():.1f}  p1 {y.quantile(.01):.0f}  p50 {y.median():.0f}  p99 {y.quantile(.99):.0f}")
    print(f"  rows with y_abs < 70: {(rows.y_abs < 70).sum()}   night_flag rows: {int(rows.night_flag.sum())}")

    out = data / "dataset.csv"
    rows.to_csv(out, index=False, float_format="%.4f", date_format="%Y-%m-%dT%H:%M:%S")
    print(f"\nwrote {out}  ({time.perf_counter() - t0:.1f} s)")


if __name__ == "__main__":
    main()
