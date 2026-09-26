"""george.md step 3.6: human review of EVAL lows only. One plot per held-out
low (glucose from 3 h before to 2 h after the crossing, the shipped model's
forecast plotted at the time it forecasts, the 85 warning threshold and 70,
the warning start), written to ml/data/processed/review_lows/ (gitignored:
real glucose), plus ml/data/review_flags.csv for Chris, who wore the sensor,
to mark obvious artifacts (steep midnight V, instant full recovery, no
treatment). Flagged lows leave the evaluation denominator (evaluate.py reads
the file); training data stays untouched. An existing flags file is never
overwritten, so Chris's answers survive a rerun.

Prints summaries only: low number, night or day, outcome, lead, and a yes/no
"fast recovery" hint. Times, values, and dates appear only in the gitignored
plots and the flags file on this laptop."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from ml.events import LOW, forecasts, replay_events
from ml.models.features import drop_collisions
from ml.train import MODELS, forecaster, holdout_start

THRESHOLD = 85.0          # the shipped operating point (metrics.md, checkpoint 4)
BEFORE_MIN, AFTER_MIN = 180, 120
FLAG_COLS = ["low_id", "crossing", "outcome", "flag_artifact", "note"]
FAST_RECOVERY = 4.0      # mg/dL/min: about the fastest real glucose moves (george.md step 1)


def shape(ts: np.ndarray, x: np.ndarray, i: int) -> dict:
    """Artifact hints around the crossing at reading i: fall rate over the 15
    min before it, the nadir, minutes under 70, and the rise rate over the 15
    min after the nadir (a compression low tends to fall and recover fast)."""
    t = ts.astype("datetime64[s]").astype(np.int64)
    below = i
    while below + 1 < len(x) and x[below + 1] < LOW and t[below + 1] - t[below] <= 30 * 60:
        below += 1
    run = slice(i, below + 1)
    nadir_k = i + int(np.argmin(x[run]))

    def rate(a_sec, b_sec):
        ia, ib = np.searchsorted(t, a_sec), np.searchsorted(t, b_sec, "right") - 1
        if ia >= len(t) or ib < 0 or ib <= ia:
            return None
        return float((x[ib] - x[ia]) / ((t[ib] - t[ia]) / 60))

    return {
        "fall_15_before": rate(t[i] - 15 * 60, t[i]),
        "nadir": float(x[nadir_k]),
        "min_below_70": float((t[below] - t[i]) / 60 + 5),
        "rise_15_after_nadir": rate(t[nadir_k], t[nadir_k] + 15 * 60),
    }


def plot_low(path: Path, ts: np.ndarray, x: np.ndarray, preds: np.ndarray, low: dict, title: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    c = low["time"]
    win = (ts >= c - np.timedelta64(BEFORE_MIN, "m")) & (ts <= c + np.timedelta64(AFTER_MIN, "m"))
    rel = (ts[win] - c).astype("timedelta64[s]").astype(float) / 60
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.plot(rel, x[win], marker=".", lw=1.5, label="glucose (sensor)")
    ax.plot(rel + 30, preds[win], ls="--", lw=1.2, label="model forecast, drawn at the time it forecasts")
    ax.axhline(THRESHOLD, color="orange", lw=1, label=f"warning threshold {THRESHOLD:.0f}")
    ax.axhline(LOW, color="red", lw=1, label="70")
    ax.axvline(0, color="red", lw=0.8, ls=":")
    if low["lead_min"] is not None:
        ax.axvline(-low["lead_min"], color="orange", lw=1.5, ls=":", label=f"warning start ({low['lead_min']:.0f} min before)")
    ax.set_xlabel("minutes from crossing 70")
    ax.set_ylabel("mg/dL")
    ax.set_title(title)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=7, loc="upper right")
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=110)
    plt.close(fig)


def held_out_lows(ts: np.ndarray, x: np.ndarray, preds: np.ndarray, start) -> tuple[dict, list[dict]]:
    rep = replay_events(ts, x, preds, threshold=THRESHOLD)
    lows = [e for e in rep["lows"] if e["time"] >= np.datetime64(start, "s")]
    return rep, lows


def load_flags(path: Path) -> set:
    """Crossing timestamps Chris flagged as artifacts (flag_artifact = y/yes/1)."""
    if not path.exists():
        return set()
    f = pd.read_csv(path, dtype=str).fillna("")
    hit = f.flag_artifact.str.strip().str.lower().isin({"y", "yes", "1", "true"})
    return {np.datetime64(pd.Timestamp(v), "s") for v in f.crossing[hit]}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", default="ml/data")
    args = p.parse_args()
    data = Path(args.data)
    import xgboost as xgb

    clean = pd.read_csv(data / "clean.csv", parse_dates=["timestamp"]).sort_values("timestamp", ignore_index=True)
    ts_all = clean.timestamp.to_numpy("datetime64[s]")
    keep = drop_collisions(ts_all.astype(np.int64))
    ts, x = ts_all[keep], clean.mgdl.to_numpy(float)[keep]
    booster = xgb.Booster()
    booster.load_model(str(MODELS / "forecast_v1.json"))
    preds = forecasts(ts, x, forecaster(booster))
    split = holdout_start(clean.timestamp)
    _, lows = held_out_lows(ts, x, preds, split.to_datetime64())

    out_dir = data / "processed" / "review_lows"
    records = []
    print(f"HELD-OUT LOWS: {len(lows)} (model = forecast_v1.json, warning threshold {THRESHOLD:.0f})")
    print("  id  night  outcome   lead  fast recovery (> 4 mg/dL/min after the lowest reading)")
    for k, low in enumerate(lows, 1):
        sh = shape(ts, x, low["i"])
        name = f"low_{k:02d}.png"
        plot_low(out_dir / name, ts, x, preds, low,
                 f"held-out low {k}  ({'night' if low['night'] else 'day'}, {low['outcome']})")
        lead = "   -" if low["lead_min"] is None else f"{low['lead_min']:4.0f}"
        fast = sh["rise_15_after_nadir"] is not None and sh["rise_15_after_nadir"] > FAST_RECOVERY
        print(f"  {k:2d}  {'yes' if low['night'] else 'no ':5}  {low['outcome']:<8} {lead}  {'yes' if fast else 'no'}")
        records.append({"low_id": k, "crossing": str(pd.Timestamp(low["time"])), "outcome": low["outcome"],
                        "flag_artifact": "", "note": ""})

    flags = data / "review_flags.csv"
    if flags.exists():
        print(f"\n{flags} exists: left untouched ({len(load_flags(flags))} flagged so far)")
    else:
        pd.DataFrame(records, columns=FLAG_COLS).to_csv(flags, index=False)
        print(f"\nwrote {flags}: Chris sets flag_artifact = y for an obvious sensor artifact, with a note")
    print(f"wrote {len(lows)} plots to {out_dir}")


if __name__ == "__main__":
    main()
