"""george.md step 6.1 helper: PROPOSE candidate dates where a basal increase
most likely took effect, for Chris to CONFIRM against an independent source
(pharmacy refills, portal messages, memory). A proposed date is a hypothesis,
never a fact: only dates Chris vouches for go into ml/data/therapy_changes.txt
as confirmed; any date used without confirmation is reported in metrics.md as
"change date inferred from the data".

Method, chosen to stay independent of the rule it will be used to test:
Basal Check fires on the overnight RISE of clean nights, so candidates are
found from a different signal, a sustained drop in the overnight LEVEL
(median glucose 00:00-07:00) between the 14 nights before a date and the 14
nights starting 2 nights after it (long-acting basal needs 2-3 days to reach
a new steady state). Each side needs at least 10 adequate-coverage nights, so
the other-brand CGM gaps drop out by themselves; candidates are kept at least
30 days apart. The rise before/after is printed for context, not used to rank.

Reads ml/data/clean.csv and nights_labeled.csv (run ml/label_history.py
first). Prints candidate dates and window medians only."""

from __future__ import annotations

import argparse
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from ml.models.features import drop_collisions

SIDE = 14
SETTLE = 2
MIN_NIGHTS = 10
MIN_APART_DAYS = 30
TOP = 6


def overnight_level(clean: pd.DataFrame) -> pd.Series:
    """Median glucose 00:00-07:00 per night, indexed by the night's evening date."""
    ts = clean.timestamp.to_numpy("datetime64[s]")
    keep = drop_collisions(ts.astype(np.int64))
    df = clean[keep]
    early = df[df.timestamp.dt.hour < 7]
    evening = (early.timestamp - pd.Timedelta(days=1)).dt.date
    return early.groupby(evening).mgdl.median()


def candidates(nights: pd.DataFrame, level: pd.Series) -> pd.DataFrame:
    n = nights[~nights.stale].copy()
    n["night_date"] = pd.to_datetime(n.night_date).dt.date
    n = n.set_index("night_date")
    n["level"] = level.reindex(n.index)
    n = n.dropna(subset=["level"])
    dates = sorted(n.index)
    rows = []
    for d in dates:
        before = n[(n.index >= d - timedelta(days=SIDE)) & (n.index < d)]
        after = n[(n.index >= d + timedelta(days=SETTLE)) & (n.index < d + timedelta(days=SETTLE + SIDE))]
        if len(before) < MIN_NIGHTS or len(after) < MIN_NIGHTS:
            continue
        rows.append({
            "date": d,
            "level_before": before.level.median(), "level_after": after.level.median(),
            "drop": before.level.median() - after.level.median(),
            "rise_before": before.rise_mgdl.median(), "rise_after": after.rise_mgdl.median(),
            "low_point_before": before.low_point_mgdl.median(), "low_point_after": after.low_point_mgdl.median(),
            "nights_before": len(before), "nights_after": len(after),
        })
    c = pd.DataFrame(rows).sort_values("drop", ascending=False)
    picked = []
    for _, r in c.iterrows():
        if r["drop"] <= 0:
            break
        if all(abs((r["date"] - p["date"]).days) >= MIN_APART_DAYS for p in picked):
            picked.append(r)
        if len(picked) == TOP:
            break
    return pd.DataFrame(picked)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", default="ml/data")
    args = p.parse_args()
    data = Path(args.data)
    clean = pd.read_csv(data / "clean.csv", parse_dates=["timestamp"]).sort_values("timestamp", ignore_index=True)
    nights = pd.read_csv(data / "nights_labeled.csv")
    top = candidates(nights, overnight_level(clean))
    print(f"CANDIDATE basal-effect dates (largest sustained drop in overnight level, 00:00-07:00 median;"
          f" {SIDE} nights before vs {SIDE} nights from day +{SETTLE}; >= {MIN_APART_DAYS} days apart)")
    print("  rank  date         level before -> after (drop)   rise before -> after   low point before -> after   nights b/a")
    for k, r in enumerate(top.itertuples(), 1):
        print(f"  {k:4d}  {r.date}   {r.level_before:5.0f} -> {r.level_after:5.0f} ({r.drop:+4.0f})"
              f"      {r.rise_before:+5.0f} -> {r.rise_after:+5.0f}"
              f"        {r.low_point_before:5.0f} -> {r.low_point_after:5.0f}            {r.nights_before}/{r.nights_after}")
    print("\nThese are HYPOTHESES from the data. Chris confirms each against pharmacy refills, portal messages,"
          "\nor memory; only confirmed dates go into ml/data/therapy_changes.txt as basal increases.")


if __name__ == "__main__":
    main()
