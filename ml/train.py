"""george.md step 3: split by TIME (hold out the final 8 weeks); XGBoost on the
30-min DELTA over the 16 features (NaN = a dropped reading, routed as missing),
trained as a QUANTILE forecast (George, checkpoint 3): the model predicts the
QUANTILE-th percentile of glucose 30 min ahead, "how low could this plausibly
go", because the squared-error model forecast the mean outcome, learned that
lows get treated and rebound, and never predicted a low (0-4% detection).
Point MAE is therefore biased high by design; events are the judge.

save ONLY ml/models/forecast_v1.json (never a pickle) with the pinned xgboost
version, plus forecast_v1_check.json: one fixed SYNTHETIC window and the value
predict() must return for it, so the Pi can prove it loads the same model.

Training never sees the evaluation period: train rows end 35 min before the
split (a row's label sits up to 32.3 min after it), and early stopping uses the last
4 weeks of the TRAIN period, never the held-out weeks. Prints summaries only."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from ml.models.features import FEATURE_NAMES, WINDOW_LEN, features

HOLDOUT_DAYS = 56
PURGE = pd.Timedelta(minutes=35)   # labels land 27.5-32.3 min after t (slot rounding); 35 covers the longest
VAL_DAYS = 28
PARAMS = {
    "objective": "reg:quantileerror",
    "eta": 0.05,
    "max_depth": 6,
    "min_child_weight": 10,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "lambda": 1.0,
    "tree_method": "hist",
    "seed": 0,
    "nthread": 4,
}
QUANTILE = 0.2           # default percentile; evaluate.py --quantile sweeps it
MAX_ROUNDS = 1500
EARLY_STOP = 50
MODELS = Path(__file__).resolve().parent / "models"
# The Pi check: a fixed, SYNTHETIC falling night window with one dropped reading.
CHECK_WINDOW = [130, 127, 125, 122, 118, np.nan, 111, 107, 104, 100, 97, 93, 90]
CHECK_HOUR = 3.5


def holdout_start(ts: pd.Series) -> pd.Timestamp:
    return ts.max() - pd.Timedelta(days=HOLDOUT_DAYS)


def rows_before(rows: pd.DataFrame, start: pd.Timestamp) -> pd.DataFrame:
    """Training rows for a model evaluated from `start` on: each row's label
    (27.5-32.3 min after t) must land before `start`, hence a 35-min purge."""
    return rows[rows.timestamp < start - PURGE]


def fit(train: pd.DataFrame, quantile: float = QUANTILE) -> tuple:
    """Early-stop on the last VAL_DAYS of `train` (time-ordered), then refit on
    all of `train` with that many rounds. Returns (booster, info)."""
    import xgboost as xgb

    params = {**PARAMS, "quantile_alpha": quantile}

    cut = train.timestamp.max() - pd.Timedelta(days=VAL_DAYS)
    tr, va = train[train.timestamp < cut - PURGE], train[train.timestamp >= cut]
    dtr = xgb.DMatrix(tr[FEATURE_NAMES].to_numpy(float), label=tr.y_delta.to_numpy(float))
    dva = xgb.DMatrix(va[FEATURE_NAMES].to_numpy(float), label=va.y_delta.to_numpy(float))
    probe = xgb.train(params, dtr, MAX_ROUNDS, evals=[(dva, "val")], early_stopping_rounds=EARLY_STOP, verbose_eval=False)
    rounds = probe.best_iteration + 1
    dall = xgb.DMatrix(train[FEATURE_NAMES].to_numpy(float), label=train.y_delta.to_numpy(float))
    booster = xgb.train(params, dall, rounds)
    return booster, {"rows": len(train), "rounds": rounds, "val_loss": float(probe.best_score), "quantile": quantile}


def forecaster(booster):
    """The model as an events/evaluate forecaster: X -> absolute value at t+30."""
    import xgboost as xgb

    cur = FEATURE_NAMES.index("current")
    return lambda X: X[:, cur] + booster.predict(xgb.DMatrix(np.asarray(X, dtype=float)))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", default="ml/data")
    p.add_argument("--quantile", type=float, default=QUANTILE)
    args = p.parse_args()
    data = Path(args.data)
    t0 = time.perf_counter()
    import xgboost as xgb

    clean_ts = pd.read_csv(data / "clean.csv", usecols=["timestamp"], parse_dates=["timestamp"]).timestamp
    rows = pd.read_csv(data / "dataset.csv", parse_dates=["timestamp"])
    split = holdout_start(clean_ts)
    train = rows_before(rows, split)
    booster, info = fit(train, args.quantile)
    print(f"xgboost {xgb.__version__}   split {split:%Y-%m-%d %H:%M} (final {HOLDOUT_DAYS} days held out)")
    print(f"objective: quantile {info['quantile']:.2f} of glucose at t+30")
    print(f"train rows {info['rows']} (label before split)   rounds {info['rounds']} (early stop on last {VAL_DAYS} train days,"
          f" val pinball loss {info['val_loss']:.2f})")

    gain = booster.get_score(importance_type="gain")
    total = sum(gain.values())
    top = sorted(((v / total, FEATURE_NAMES[int(k[1:])]) for k, v in gain.items()), reverse=True)
    print("feature gain share: " + ", ".join(f"{n} {100 * g:.0f}%" for g, n in top[:8]))

    MODELS.mkdir(exist_ok=True)
    out = MODELS / "forecast_v1.json"
    booster.save_model(str(out))
    x = features(np.asarray(CHECK_WINDOW, dtype=float), CHECK_HOUR).reshape(1, -1)
    expected = float(CHECK_WINDOW[-1] + booster.predict(xgb.DMatrix(x))[0])

    # Round trip through predict.py exactly as the Pi will call it.
    import importlib

    from ml.models import predict as predict_mod

    predict_mod = importlib.reload(predict_mod)              # predict.py loads the model at import
    got = predict_mod.predict(CHECK_WINDOW, CHECK_HOUR)
    check = {"window": [None if np.isnan(v) else v for v in np.asarray(CHECK_WINDOW, dtype=float)],
             "hour": CHECK_HOUR, "expected": expected, "xgboost": xgb.__version__, "quantile": info["quantile"],
             "note": "SYNTHETIC window; predict(window with None -> NaN, hour) must return expected within 1e-3"}
    (MODELS / "forecast_v1_check.json").write_text(json.dumps(check, indent=2) + "\n")
    print(f"\nwrote {out} ({out.stat().st_size / 1e6:.2f} MB)")
    print(f"Pi check: predict(CHECK_WINDOW, {CHECK_HOUR}) = {got:.3f}; in-memory booster {expected:.3f};"
          f" match {abs(got - expected) < 1e-3}   ({time.perf_counter() - t0:.1f} s)")
    assert len(CHECK_WINDOW) == WINDOW_LEN


if __name__ == "__main__":
    main()
