"""george.md step 3: time split (final 8 weeks held out), baselines FIRST, then
point metrics (MAE overall and where true < 100) and event metrics through the
actual alarm rule (ml/events.py), swept over predicted thresholds 70/75/80.

Baselines (forecasters on the 16-feature matrix, like the model):
  A persistence   current
  B linear trend  current + 30 x roc15
  C weighted ROC  current + 30 x (3 r1 + 2 r2 + 1 r3) / 6, r = the last three
                  5-min rates, newest first
Baselines have no training, so they are also scored over the full history
(more events, steadier numbers). Reads ml/data/clean.csv and dataset.csv;
writes the sweep plot to ml/data/processed/ (gitignored). Prints summaries
only. metrics.md is written once the model and threshold exist (step 3.7)."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd

from ml.build_dataset import drop_collisions
from ml.events import (FCST_FROM_MIN, FCST_TO_MIN, MAX_LEAD_MIN, MIN_LEAD_MIN, NEAR_MISS_MGDL, forecasts,
                       replay_events, summarize)
from ml.models.features import FEATURE_NAMES

HOLDOUT_DAYS = 56
THRESHOLDS = (70.0, 75.0, 80.0)
N_CONSEC = 2
_F = {n: k for k, n in enumerate(FEATURE_NAMES)}


def persistence(X: np.ndarray) -> np.ndarray:
    return X[:, _F["current"]]


def linear_trend(X: np.ndarray) -> np.ndarray:
    return X[:, _F["current"]] + 30 * X[:, _F["roc15"]]


def weighted_roc(X: np.ndarray) -> np.ndarray:
    r1 = X[:, _F["roc5"]]
    r2 = (X[:, _F["lag5"]] - X[:, _F["lag10"]]) / 5
    r3 = (X[:, _F["lag10"]] - X[:, _F["lag15"]]) / 5
    return X[:, _F["current"]] + 30 * (3 * r1 + 2 * r2 + r3) / 6


BASELINES = {"A persistence": persistence, "B linear 15m": linear_trend, "C weighted ROC": weighted_roc}


def holdout_start(ts: pd.Series) -> pd.Timestamp:
    return ts.max() - pd.Timedelta(days=HOLDOUT_DAYS)


def point_metrics(rows: pd.DataFrame, forecast) -> dict:
    X = rows[FEATURE_NAMES].to_numpy(float)
    err = np.abs(forecast(X) - rows.y_abs.to_numpy(float))
    low = rows.y_abs.to_numpy() < 100
    return {"mae": float(err.mean()), "mae_lt100": float(err[low].mean()) if low.any() else None, "n": len(err)}


def _pct(v):
    return "   -  " if v is None else f"{100 * v:5.1f}%"


def _num(v, fmt="5.1f"):
    return "  -  " if v is None else format(v, fmt)


def print_event_table(title: str, results: dict) -> None:
    print(f"\n{title}")
    for scope, unit in (("night", "night"), ("all", "day")):
        print(f"  [{scope.upper()}]  name              thr  lows fcst  det late long miss   detect  det|fcst  med lead  p90 lead  false near  far  false/{unit}  far/{unit}")
        for (name, thr), s in results.items():
            r = s[scope]
            print(f"          {name:<16} {thr:4.0f}  {r['lows']:4d} {r['forecastable']:4d} {r['detected']:4d} {r['late']:4d} {r['long']:4d} {r['missed']:4d}"
                  f"   {_pct(r['detection'])}  {_pct(r['detection_forecastable'])}   {_num(r['median_lead_min'])}     {_num(r['p90_lead_min'])}"
                  f"   {r['false_alarms']:4d} {r['false_near']:4d} {r['false_far']:4d}   {_num(r['false_per_unit'], '6.2f')}      {_num(r['false_far_per_unit'], '6.2f')}")


def sweep_plot(full: dict, held: dict, out: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), sharey=True)
    for ax, res, title in ((axes[0], full, "full history"), (axes[1], held, f"held-out {HOLDOUT_DAYS} days")):
        for name in BASELINES:
            pts = [(res[(name, thr)]["night"]["false_per_unit"], res[(name, thr)]["night"]["detection"], thr) for thr in THRESHOLDS]
            pts = [p for p in pts if p[0] is not None and p[1] is not None]
            ax.plot([p[0] for p in pts], [100 * p[1] for p in pts], marker="o", label=name)
            for fx, dy, thr in pts:
                ax.annotate(f"{thr:.0f}", (fx, 100 * dy), textcoords="offset points", xytext=(4, 4), fontsize=8)
        ax.axvline(0.5, color="grey", ls="--", lw=1)
        ax.set_title(f"Overnight: {title}")
        ax.set_xlabel("false alarms per night")
        ax.grid(alpha=0.3)
    axes[0].set_ylabel("detection (warning 10-60 min ahead), %")
    axes[0].legend(fontsize=8)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=120)
    plt.close(fig)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", default="ml/data")
    args = p.parse_args()
    data = Path(args.data)
    t0 = time.perf_counter()

    clean = pd.read_csv(data / "clean.csv", parse_dates=["timestamp"]).sort_values("timestamp", ignore_index=True)
    ts_sec = clean.timestamp.to_numpy("datetime64[s]")
    keep = drop_collisions(ts_sec.astype(np.int64))          # the same readings the dataset was built from
    ts, x = ts_sec[keep], clean.mgdl.to_numpy(float)[keep]
    rows = pd.read_csv(data / "dataset.csv", parse_dates=["timestamp"])

    split = holdout_start(clean.timestamp)
    held_rows = rows[rows.timestamp >= split]
    print(f"TIME SPLIT: train {clean.timestamp.min():%Y-%m-%d} .. {split:%Y-%m-%d %H:%M}   "
          f"held out {split:%Y-%m-%d %H:%M} .. {clean.timestamp.max():%Y-%m-%d}  ({HOLDOUT_DAYS} days)")
    print(f"  dataset rows: train {len(rows) - len(held_rows)}   held out {len(held_rows)}")
    print(f"\nDEFINITIONS: warning = {N_CONSEC} consecutive forecasts below thr; detected = warning on at the crossing"
          f" with {MIN_LEAD_MIN:.0f} <= lead <= {MAX_LEAD_MIN:.0f} min; late < {MIN_LEAD_MIN:.0f}; long > {MAX_LEAD_MIN:.0f} (not credited);"
          f"\n  low = any reading < 70, new low after 2 readings >= 70; fcst (forecastable) = >= 2 forecasts"
          f" {FCST_FROM_MIN}..{FCST_TO_MIN} min before the crossing;"
          f"\n  false = warning cleared with no crossing; near = lowest actual during it < {NEAR_MISS_MGDL:.0f}, far = >= {NEAR_MISS_MGDL:.0f}")

    print("\nPOINT METRICS, held-out rows (absolute mg/dL at t+30)")
    print("  name              MAE    MAE(true<100)")
    for name, f in BASELINES.items():
        m = point_metrics(held_rows, f)
        print(f"  {name:<16} {m['mae']:5.2f}   {_num(m['mae_lt100'], '5.2f')}")

    full, held = {}, {}
    for name, f in BASELINES.items():
        preds = forecasts(ts, x, f)
        for thr in THRESHOLDS:
            rep = replay_events(ts, x, preds, threshold=thr, n_consecutive=N_CONSEC)
            full[(name, thr)] = summarize(rep)
            held[(name, thr)] = summarize(rep, start=split.to_datetime64())

    s = held[("B linear 15m", 70.0)]
    print(f"\nHELD-OUT coverage: {s['days']:.1f} sensor-days, {s['nights']:.1f} sensor-nights")
    print_event_table(f"EVENT METRICS, HELD-OUT {HOLDOUT_DAYS} DAYS", held)
    s = full[("B linear 15m", 70.0)]
    print(f"\nFULL HISTORY coverage: {s['days']:.1f} sensor-days, {s['nights']:.1f} sensor-nights")
    print_event_table("EVENT METRICS, FULL HISTORY (baselines are untrained, so no leakage)", full)

    out = data / "processed" / "sweep_baselines.png"
    sweep_plot(full, held, out)
    print(f"\nwrote {out}  ({time.perf_counter() - t0:.1f} s)")


if __name__ == "__main__":
    main()
