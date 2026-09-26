"""george.md step 5: label the real history. Runs the cleaned 22 months through
nights.classify_night, night_metrics, and low_events, with the shipped
forecaster (forecast_v1.json) replayed through the actual warning rule
(events.replay_events at the shipped threshold, 85) to INFER the glucose side
of each night's alarm events: warnings, whether they crossed, near-misses.

Everything here is INFERRED from glucose, and labeled so:
  - reason codes: code_source "inferred" (no treatments on history: late_meal
    and treated_low inferred; basal, exercise, away never asserted);
  - alarm events: replayed, not recorded (tier predicted_low from a warning,
    actual_low from a crossing); acknowledge, escalation, presence, and
    morning answers are NOT in the export and are never fabricated here
    (they enter only as a labeled overlay in demo scenarios, step 7);
  - forecast_in_sample: the night falls before the forecaster's train/test
    split, so its warnings come from a model that trained on that night
    (george.md step 6.4: such a warning is not evidence).

Writes ml/data/nights_labeled.csv and ml/data/lows_labeled.csv (gitignored:
real medical history). Prints summaries only: counts, and per-month counts."""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from ml.events import forecasts, replay_events
from ml.models import nights as N
from ml.models.features import drop_collisions
from ml.models.predict import PREDICTED_LOW_THRESHOLD
from ml.train import MODELS, forecaster, holdout_start

NIGHT = ("22:00", "07:00")
BEFORE_H, AFTER_H = 3, 2        # context sliced around each night: late-meal lookback, rebound / recovery after


def night_windows(first: datetime, last: datetime) -> list[tuple[datetime, datetime]]:
    sh, sm = (int(v) for v in NIGHT[0].split(":"))
    eh, em = (int(v) for v in NIGHT[1].split(":"))
    d = first.date() - timedelta(days=1)
    out = []
    while d <= last.date():
        start = datetime(d.year, d.month, d.day, sh, sm)
        end = datetime(d.year, d.month, d.day, eh, em) + timedelta(days=1)
        out.append((start, end))
        d += timedelta(days=1)
    return out


def alarm_events_from_replay(rep: dict) -> list[dict]:
    """The replay's warnings and crossings as AlarmEvent-shaped dicts, with
    only the glucose-side fields; everything a human does stays unset."""
    events = []
    crossings = {low["i"]: low for low in rep["lows"]}
    for w in rep["warnings"]:
        ts = pd.Timestamp(w["time"]).to_pydatetime()
        events.append({"event_id": f"inf-warn-{ts:%Y%m%dT%H%M%S}", "tier": "predicted_low", "started_at": ts,
                       "crossed_actual": w["end"] == "escalated", "presence_during": "unknown", "inferred": True})
    for i, low in crossings.items():
        ts = pd.Timestamp(low["time"]).to_pydatetime()
        events.append({"event_id": f"inf-low-{ts:%Y%m%dT%H%M%S}", "tier": "actual_low", "started_at": ts,
                       "crossed_actual": True, "presence_during": "unknown", "inferred": True})
    return sorted(events, key=lambda e: e["started_at"])


def label(ts: np.ndarray, x: np.ndarray, alarms: list[dict], split: datetime) -> tuple[pd.DataFrame, pd.DataFrame]:
    tsec = ts.astype("datetime64[s]").astype(np.int64)
    alarm_t = np.array([np.datetime64(a["started_at"], "s").astype(np.int64) for a in alarms], dtype=np.int64)
    first, last = pd.Timestamp(ts[0]).to_pydatetime(), pd.Timestamp(ts[-1]).to_pydatetime()
    night_rows, low_rows = [], []
    for start, end in night_windows(first, last):
        s0 = np.datetime64(start - timedelta(hours=BEFORE_H), "s").astype(np.int64)
        s1 = np.datetime64(end + timedelta(hours=AFTER_H), "s").astype(np.int64)
        lo, hi = np.searchsorted(tsec, s0), np.searchsorted(tsec, s1)
        w0, w1 = np.datetime64(start, "s").astype(np.int64), np.datetime64(end, "s").astype(np.int64)
        in_night = np.searchsorted(tsec, w1) - np.searchsorted(tsec, w0)
        if in_night == 0:
            continue                                   # no data at all (a gap night): not a night of history
        readings = [{"timestamp": pd.Timestamp(t).to_pydatetime(), "glucose_mgdl": float(v)}
                    for t, v in zip(ts[lo:hi], x[lo:hi])]
        a_lo, a_hi = np.searchsorted(alarm_t, s0), np.searchsorted(alarm_t, s1)
        ev = alarms[a_lo:a_hi]
        codes, source = N.classify_night(readings, None, None, None, (start, end))
        m = N.night_metrics(readings, ev, (start, end))
        lows = N.low_events(readings, None, ev, (start, end))
        warns = [a for a in ev if a["tier"] == "predicted_low" and w0 <= np.datetime64(a["started_at"], "s").astype(np.int64) < w1]
        night_rows.append({
            "night_date": start.date(), "coverage_pct": round(m["coverage_pct"], 2), "stale": m["stale"],
            "reason_codes": ";".join(codes), "code_source": source,
            "rise_mgdl": m["rise_mgdl"], "dawn_rise_mgdl": m["dawn_rise_mgdl"], "low_point_mgdl": m["low_point_mgdl"],
            "tbr_pct": m["tbr_pct"], "minutes_below_70": m["minutes_below_70"], "auc_below_70": m["auc_below_70"],
            "level2_count": m["level2_count"], "ketone_risk_episodes": m["ketone_risk_episodes"],
            "near_miss_count": m["near_miss_count"], "warnings_inferred": len(warns),
            "warnings_crossed": sum(a["crossed_actual"] for a in warns),
            "low_events": len(lows), "inferred_unfelt": sum(e["inferred_unfelt"] for e in lows),
            "forecast_in_sample": start < split,
        })
        for e in lows:
            low_rows.append({k: e[k] for k in ("low_event_id", "night_date", "started_at", "nadir_mgdl", "nadir_at",
                                               "minutes_below_70", "auc_below_70", "recovery_slope",
                                               "inferred_unfelt", "alarm_event_id")}
                            | {"forecast_in_sample": start < split})
    return pd.DataFrame(night_rows), pd.DataFrame(low_rows)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", default="ml/data")
    args = p.parse_args()
    data = Path(args.data)
    t0 = time.perf_counter()
    import xgboost as xgb

    clean = pd.read_csv(data / "clean.csv", parse_dates=["timestamp"]).sort_values("timestamp", ignore_index=True)
    ts_all = clean.timestamp.to_numpy("datetime64[s]")
    keep = drop_collisions(ts_all.astype(np.int64))
    ts, x = ts_all[keep], clean.mgdl.to_numpy(float)[keep]
    booster = xgb.Booster()
    booster.load_model(str(MODELS / "forecast_v1.json"))
    preds = forecasts(ts, x, forecaster(booster))
    rep = replay_events(ts, x, preds, threshold=PREDICTED_LOW_THRESHOLD)
    alarms = alarm_events_from_replay(rep)
    split = holdout_start(clean.timestamp).to_pydatetime()

    nights, lows = label(ts, x, alarms, split)
    nights.to_csv(data / "nights_labeled.csv", index=False)
    lows.to_csv(data / "lows_labeled.csv", index=False)

    ok = nights[~nights.stale]
    codes = nights.reason_codes.str.split(";").explode()
    print(f"NIGHTS with any reading: {len(nights)}   stale (< 85% coverage): {int(nights.stale.sum())}"
          f"   adequate: {len(ok)}")
    print("reason codes, INFERRED (nights may carry several): "
          + ", ".join(f"{c} {n}" for c, n in codes.value_counts().items()))
    print(f"clean nights (inferred, adequate coverage): {int((ok.reason_codes == 'clean').sum())}")
    print(f"\nLOWS (under 70 confirmed by 2 readings, starting 22:00-07:00): {int(nights.low_events.sum())} events"
          f" on {int((nights.low_events > 0).sum())} nights; inferred unfelt {int(nights.inferred_unfelt.sum())}")
    print(f"level 2 (< 54 confirmed) nights: {int((nights.level2_count > 0).sum())}"
          f"   ketone-risk episodes overnight: {int(nights.ketone_risk_episodes.sum())}")
    print(f"INFERRED warnings (forecast_v1 at {PREDICTED_LOW_THRESHOLD:.0f}): {int(nights.warnings_inferred.sum())},"
          f" crossed {int(nights.warnings_crossed.sum())}; near-misses {int(nights.near_miss_count.sum())}")
    out = nights[~nights.forecast_in_sample]
    print(f"  of which out-of-sample (nights after the split, {len(out)} nights): warnings {int(out.warnings_inferred.sum())},"
          f" near-misses {int(out.near_miss_count.sum())}")

    print("\nPER MONTH  nights  stale  clean  low-nights  lows  near-misses")
    month = pd.to_datetime(nights.night_date).dt.to_period("M")
    for mo, g in nights.groupby(month):
        adequate = g[~g.stale]
        print(f"  {mo}   {len(g):5d}  {int(g.stale.sum()):5d}  {int((adequate.reason_codes == 'clean').sum()):5d}"
              f"  {int((g.low_events > 0).sum()):10d}  {int(g.low_events.sum()):4d}  {int(g.near_miss_count.sum()):11d}")
    print(f"\nwrote {data / 'nights_labeled.csv'} and {data / 'lows_labeled.csv'}  ({time.perf_counter() - t0:.1f} s)")


if __name__ == "__main__":
    main()
