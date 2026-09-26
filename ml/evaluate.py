"""george.md step 3: time split (final 8 weeks held out), baselines FIRST, then
the model; point metrics (MAE overall and where true < 100) and event metrics
through the actual alarm rule (ml/events.py), swept over predicted thresholds.

Baselines (forecasters on the 16-feature matrix, like the model):
  A persistence   current
  B linear trend  current + 30 x roc15 (a missing slot: the 10-min, then 30-min rate)
  C weighted ROC  current + 30 x (3 r1 + 2 r2 + 1 r3) / 6, r = the last three
                  5-min rates, newest first (falls back to B on a missing slot)
M xgboost (a quantile forecast, --quantile) is scored only where it never trained:
  - HELD-OUT: the final 8 weeks, forecast by the last fold's model, which is
    trained exactly as train.py trains the shipped forecast_v1.json;
  - ROLLING FOLDS: FOLDS consecutive 8-week blocks ending with the held-out
    one; each block is forecast by a model trained only on rows whose label
    lands before the block starts (train.rows_before), so every forecast is
    out-of-sample and the pooled blocks hold several times the held-out lows.
Baselines have no training, so they are also scored over the full history.
Reads ml/data/clean.csv and dataset.csv; writes the sweep plot to ml/data/processed/ (gitignored).
Prints summaries only. metrics.md waits for the threshold choice (step 3.7)."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd

from ml.events import (FCST_FROM_MIN, FCST_TO_MIN, MAX_LEAD_MIN, MIN_LEAD_MIN, NEAR_MISS_MGDL, forecasts,
                       replay_events, summarize)
from ml.models.features import FEATURE_NAMES, drop_collisions
from ml.train import HOLDOUT_DAYS, QUANTILE, fit, forecaster, holdout_start, rows_before

THRESHOLDS = (70.0, 75.0, 80.0, 85.0, 90.0)
N_CONSEC = 2
FOLDS = 5
MODEL = "M xgboost"
_F = {n: k for k, n in enumerate(FEATURE_NAMES)}


def persistence(X: np.ndarray) -> np.ndarray:
    return X[:, _F["current"]]


def linear_trend(X: np.ndarray) -> np.ndarray:
    """current + 30 x the 15-min rate; with that slot empty, the 10-min rate,
    then the 30-min rate; NaN (no forecast) when all three are missing."""
    cur = X[:, _F["current"]]
    rate = X[:, _F["roc15"]]
    rate = np.where(np.isnan(rate), (cur - X[:, _F["lag10"]]) / 10, rate)
    rate = np.where(np.isnan(rate), X[:, _F["roc30"]], rate)
    return cur + 30 * rate


def weighted_roc(X: np.ndarray) -> np.ndarray:
    """current + 30 x (3 r1 + 2 r2 + r3) / 6; falls back to linear_trend when
    any of the three 5-min rates needs an empty slot."""
    r1 = X[:, _F["roc5"]]
    r2 = (X[:, _F["lag5"]] - X[:, _F["lag10"]]) / 5
    r3 = (X[:, _F["lag10"]] - X[:, _F["lag15"]]) / 5
    out = X[:, _F["current"]] + 30 * (3 * r1 + 2 * r2 + r3) / 6
    return np.where(np.isnan(out), linear_trend(X), out)


BASELINES = {"A persistence": persistence, "B linear 15m": linear_trend, "C weighted ROC": weighted_roc}


def point_metrics(rows: pd.DataFrame, forecast) -> dict:
    X = rows[FEATURE_NAMES].to_numpy(float)
    err = np.abs(forecast(X) - rows.y_abs.to_numpy(float))
    low = rows.y_abs.to_numpy() < 100
    has = ~np.isnan(err)                                   # a baseline may have no forecast on a holey window
    return {"mae": float(err[has].mean()), "mae_lt100": float(err[has & low].mean()) if (has & low).any() else None,
            "n": int(has.sum()), "no_forecast": int((~has).sum())}


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


def sweep_plot(panels: list[tuple[str, dict]], out: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, len(panels), figsize=(5.5 * len(panels), 4.5), sharey=True)
    for ax, (title, res) in zip(axes, panels):
        for name in dict.fromkeys(n for n, _ in res):
            pts = [(res[(name, thr)]["night"]["false_per_unit"], res[(name, thr)]["night"]["detection"], thr) for thr in THRESHOLDS]
            pts = [p for p in pts if p[0] is not None and p[1] is not None]
            ax.plot([p[0] for p in pts], [100 * p[1] for p in pts], marker="o", label=name, lw=2.5 if name == MODEL else 1.5)
            for fx, dy, thr in pts:
                ax.annotate(f"{thr:.0f}", (fx, 100 * dy), textcoords="offset points", xytext=(4, 4), fontsize=8)
        ax.axvline(0.5, color="grey", ls="--", lw=1)
        ax.set_title(f"Overnight: {title}")
        ax.set_xlabel("false alarms per night")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
    axes[0].set_ylabel("detection (warning 10-60 min ahead), %")
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=120)
    plt.close(fig)


def score(ts, x, preds_by_name: dict, start=None) -> dict:
    out = {}
    for name, preds in preds_by_name.items():
        for thr in THRESHOLDS:
            rep = replay_events(ts, x, preds, threshold=thr, n_consecutive=N_CONSEC)
            out[(name, thr)] = summarize(rep, start=start)
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", default="ml/data")
    p.add_argument("--quantile", type=float, default=QUANTILE)
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

    base_preds = {name: forecasts(ts, x, f) for name, f in BASELINES.items()}
    full = score(ts, x, base_preds)

    # Rolling folds: block k is forecast by a model trained only before block k.
    # The last block is the held-out 8 weeks; its model is the shipped model's twin.
    starts = [split - pd.Timedelta(days=HOLDOUT_DAYS * k) for k in range(FOLDS - 1, -1, -1)]
    ends = starts[1:] + [clean.timestamp.max() + pd.Timedelta(seconds=1)]
    pooled = np.full(len(x), np.nan)
    print(f"\nROLLING FOLDS ({FOLDS} x {HOLDOUT_DAYS} days, each forecast by a model trained only before it;"
          f" model = quantile {args.quantile:.2f})")
    for a, b in zip(starts, ends):
        booster, info = fit(rows_before(rows, a), args.quantile)
        f = forecaster(booster)
        in_block = (ts >= a.to_datetime64()) & (ts < b.to_datetime64())
        pooled[in_block] = forecasts(ts, x, f)[in_block]
        block = rows[(rows.timestamp >= a) & (rows.timestamp < b)]
        mb, mm = point_metrics(block, linear_trend), point_metrics(block, f)
        print(f"  {a:%Y-%m-%d} .. {b:%Y-%m-%d}   train rows {info['rows']:6d}  rounds {info['rounds']:4d}"
              f"   MAE B {mb['mae']:5.2f}  M {mm['mae']:5.2f}   (true<100: B {_num(mb['mae_lt100'], '5.2f')}  M {_num(mm['mae_lt100'], '5.2f')})")
    held_model = f                                           # the last block's model = the held-out model

    print("\nPOINT METRICS, held-out rows (absolute mg/dL at t+30; M is a low quantile, so biased low by design)")
    print("  name              MAE    MAE(true<100)  no forecast")
    for name, fn in {**BASELINES, MODEL: held_model}.items():
        m = point_metrics(held_rows, fn)
        print(f"  {name:<16} {m['mae']:5.2f}   {_num(m['mae_lt100'], '5.2f')}          {m['no_forecast']}")

    two = {n: base_preds[n] for n in ("B linear 15m", "C weighted ROC")}
    folds = score(ts, x, {**two, MODEL: pooled}, start=starts[0].to_datetime64())
    held = score(ts, x, {**base_preds, MODEL: pooled}, start=split.to_datetime64())

    s = held[("B linear 15m", 70.0)]
    print(f"\nHELD-OUT coverage: {s['days']:.1f} sensor-days, {s['nights']:.1f} sensor-nights")
    print_event_table(f"EVENT METRICS, HELD-OUT {HOLDOUT_DAYS} DAYS", held)
    s = folds[("B linear 15m", 70.0)]
    print(f"\nROLLING-FOLD coverage: {s['days']:.1f} sensor-days, {s['nights']:.1f} sensor-nights")
    print_event_table(f"EVENT METRICS, ROLLING FOLDS POOLED ({FOLDS} x {HOLDOUT_DAYS} days, all out-of-sample)", folds)
    s = full[("B linear 15m", 70.0)]
    print(f"\nFULL HISTORY coverage: {s['days']:.1f} sensor-days, {s['nights']:.1f} sensor-nights")
    print_event_table("EVENT METRICS, FULL HISTORY (baselines only: untrained, so no leakage)", full)

    out = data / "processed" / f"sweep_q{round(100 * args.quantile):02d}.png"
    sweep_plot([("full history, baselines", full), (f"rolling folds ({FOLDS} x 8 wk)", folds),
                (f"held-out {HOLDOUT_DAYS} days", held)], out)
    print(f"\nwrote {out}  ({time.perf_counter() - t0:.1f} s)")


if __name__ == "__main__":
    main()
