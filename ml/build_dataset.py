"""george.md step 2: one row per reading only when a usable 60-min window AND the t+30 label sit inside one gap-free stretch; label = glucose(t+30) - glucose(t) (the DELTA); features via models/features.py ONLY; no future information; prints row count and NaN check.

Windows are 13 five-minute slots built by features.grid_slots + slot_windows, the same lines the event replay and the Pi run. A dropped reading leaves its slot NaN (never interpolated); a window is usable with the current reading present, no more than 5 empty slots in a row, and no gap (> 30 min) inside it. The label needs a reading in the exact slot 30 min later, in the same stretch. Reads ml/data/clean.csv, writes ml/data/dataset.csv (gitignored). Prints summaries only."""

import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd

from ml.models.features import (FEATURE_NAMES, GAP_MIN, HORIZON_SLOTS, WINDOW_LEN, drop_collisions,  # noqa: F401 (re-export)
                                features, grid_slots, slot_windows)

LABEL_COLS = ["current", "y_delta"]


def build(ts: pd.Series, mgdl: pd.Series) -> tuple[pd.DataFrame, dict]:
    """ts sorted ascending, naive local time. Returns (rows, stats)."""
    ts = pd.Series(pd.to_datetime(ts)).reset_index(drop=True)
    ts_sec_all = ts.to_numpy("datetime64[s]").astype(np.int64)
    keep, gslot, stretch = grid_slots(ts_sec_all)
    ts, ts_sec = ts[keep].reset_index(drop=True), ts_sec_all[keep]
    x = np.asarray(mgdl, dtype=float)[keep]
    n = len(x)
    stats = {"readings": len(keep), "collision_dropped": int((~keep).sum()),
             "stretches": int(stretch[-1] + 1) if n else 0}
    empty = pd.DataFrame(columns=["timestamp", "stretch", *FEATURE_NAMES, "y_delta", "y_abs"])
    if n == 0:
        return empty, stats

    windows, ok = slot_windows(gslot, stretch, x)
    target = gslot + HORIZON_SLOTS
    j = np.searchsorted(gslot, target)
    j_safe = np.minimum(j, n - 1)
    has_label = (j < n) & (gslot[j_safe] == target)
    kept = ok & has_label

    # Why readings have no window: the stretch's first hour (warm-up after a
    # sensor change or gap), or too many missing readings (> 30 min) inside it.
    first = np.full(int(stretch[-1]) + 1, -1, np.int64)
    first[stretch[::-1]] = gslot[::-1]
    warm = gslot - (WINDOW_LEN - 1) < first[stretch]
    has_hole = np.isnan(windows).any(axis=1)
    stats.update(
        candidates=n,
        no_window_warmup=int((~ok & warm).sum()),
        no_window_hole=int((~ok & ~warm).sum()),
        window_with_missing_slot=int((ok & has_hole).sum()),
        window_ok_no_label=int((ok & ~has_label).sum()),
        rows=int(kept.sum()),
    )

    ti, ji = np.flatnonzero(kept), j[kept]
    hour = (ts.dt.hour + ts.dt.minute / 60 + ts.dt.second / 3600).to_numpy()[ti]
    X = features(windows[kept], hour)
    rows = pd.DataFrame(X, columns=FEATURE_NAMES)
    rows.insert(0, "timestamp", ts.to_numpy()[ti])
    rows.insert(1, "stretch", stretch[ti])
    rows["y_delta"] = x[ji] - x[ti]
    rows["y_abs"] = x[ji]

    # Independent checks on what was built (printed, and asserted in tests),
    # from real timestamps: the same slot grid filled with each reading's time.
    pad = WINDOW_LEN - 1
    tgrid = np.full(int(gslot[-1]) + 1 + pad, np.nan)
    tgrid[gslot + pad] = ts_sec
    w_ts = np.lib.stride_tricks.sliding_window_view(tgrid, WINDOW_LEN)[gslot[kept]]
    t, lab = ts_sec[ti], ts_sec[ji]
    oldest = np.nanmin(w_ts, axis=1)
    # A row crosses a gap iff some gap starts at a reading from its oldest
    # window reading up to (not including) its label reading.
    gap_start = ts_sec[:-1][np.diff(ts_sec) > GAP_MIN * 60]
    crossings = np.searchsorted(gap_start, lab, "left") - np.searchsorted(gap_start, oldest, "left")
    stats.update(
        nan_cells=int(rows[LABEL_COLS].isna().sum().sum()),
        nan_by_feature={c: int(v) for c, v in rows[FEATURE_NAMES].isna().sum().items() if v},
        rows_touching_gap=int((crossings > 0).sum()),
        oldest_input_min=(float(((t - oldest) / 60).min()), float(((t - oldest) / 60).max())) if len(t) else (0.0, 0.0),
        label_ahead_min=(float(((lab - t) / 60).min()), float(((lab - t) / 60).max())) if len(t) else (0.0, 0.0),
        inputs_all_at_or_before_t=bool((np.nanmax(w_ts, axis=1) == t).all()),
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
    print(f"  over 30 min of missing readings inside a stretch:         {s['no_window_hole']}"
          f"  ({100 * s['no_window_hole'] / total:.2f}% of readings)")
    print(f"usable windows with at least one missing slot (NaN features): {s['window_with_missing_slot']}")
    print(f"window ok but no reading exactly 30 min later: {s['window_ok_no_label']}")
    print(f"\nROWS: {s['rows']}  ({100 * s['rows'] / total:.1f}% of readings)")
    print(f"features: {len(FEATURE_NAMES)}  {', '.join(FEATURE_NAMES)}")

    print("\nCHECKS")
    print(f"  NaN cells in current + label:             {s['nan_cells']}")
    print(f"  NaN cells per feature (missing slots):     {s['nan_by_feature'] or 'none'}")
    print(f"  rows whose window..label crosses a gap:      {s['rows_touching_gap']}")
    print(f"  oldest window input before t (min):         {s['oldest_input_min'][0]:.2f} .. {s['oldest_input_min'][1]:.2f}")
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
