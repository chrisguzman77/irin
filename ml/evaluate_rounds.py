"""george.md step 6: the three measured numbers from the real history, never
from a synthetic scenario:
  1. retrospective LEAD TIME of Basal Check before each listed therapy change;
  2. FALSE-ALARM (firing) RATE per signal over stable-therapy stretches;
  3. DETECTION LAG of the low-point-shift rule after each change;
with the leakage guard (step 6.4) and the n = 1, observational,
retrospective caveats.

Every metric comes from ml/models/nights.py (via the labeled nights from
ml/label_history.py and nights.standing_window / step_window_metrics). The
RULE THRESHOLDS below mirror docs/plans/chris.md R8 and R10 verbatim
because backend/app/rounds/standing.py and step_watch.py are still stubs;
when they land, their evaluate_* functions replace the few `_fires_*` lines
here and this script is rerun.

Inputs (all gitignored, never printed raw): ml/data/nights_labeled.csv,
lows_labeled.csv, clean.csv, and therapy_changes.txt (date, kind,
confirmation, uncertainty; a confirmation starting INFERRED marks a date
picked from the data, reported separately and never in a headline).
Near-miss signals rest on forecast_v1's replayed warnings, which are
in-sample before the forecaster's split; they are reported over all
stable windows AND over out-of-sample windows only. Prints summaries only."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from ml.models import nights as N
from ml.models.features import drop_collisions

# chris.md R8 Basal Check / R10 Step Watch thresholds (starting values, tuned at checkpoint 6)
BASAL_NIGHTS = 14
BASAL_MIN_CLEAN = 5
BASAL_RISE = 30.0               # fires beyond +/-30 (+30 does not fire, +31 does)
BASAL_SHARE = 0.70
BASAL_NEAR_MISSES = 3           # "possibly too high" direction
STEP_DAYS = 5
BASELINE_NIGHTS = 14
BASELINE_MIN = 5
SHIFT = -15.0                   # low-point shift <= -15 fires
TBR = 4.0                       # > 4.0% fires
STEP_NEAR_MISSES = 2
KETONE_EPISODES = 2
DETECT_SHIFT = 15.0             # detection lag: |shift| >= 15 either direction
STABLE_MARGIN_DAYS = 30         # windows within 30 days of a change are not "stable"
LEAD_LOOKBACK_DAYS = 90
DETECT_MAX_DAYS = 45


@dataclass
class Change:
    day: date
    kind: str
    confirmed: bool
    note: str


def read_changes(path: Path) -> list[Change]:
    out = []
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split(None, 3)
        out.append(Change(date.fromisoformat(parts[0]), parts[1], not parts[2].upper().startswith("INFERRED"),
                          " ".join(parts[2:])))
    return sorted(out, key=lambda c: c.day)


def load_records(path: Path) -> list[dict]:
    n = pd.read_csv(path)
    recs = []
    for r in n.to_dict("records"):
        r["night_date"] = date.fromisoformat(str(r["night_date"]))
        r["reason_codes"] = str(r["reason_codes"]).split(";")
        for k in ("rise_mgdl", "low_point_mgdl"):
            r[k] = None if pd.isna(r[k]) else float(r[k])
        recs.append(r)
    return recs


def nights_in(recs: list[dict], lo: date, hi: date) -> list[dict]:
    return [r for r in recs if lo <= r["night_date"] < hi]


# ---------------------------------------------------------------- the rules (chris.md R8 / R10)

def basal_check(window: list[dict]) -> dict:
    """Basal Check over one 14-night window: evaluable, and which direction fires."""
    v, _ = N.standing_window(window, [], [])
    evaluable = v["clean_nights"] >= BASAL_MIN_CLEAN and v["rise_median_clean"] is not None
    share = v["same_direction_share"] or 0.0
    up = evaluable and v["rise_median_clean"] > BASAL_RISE and share >= BASAL_SHARE
    down = evaluable and v["rise_median_clean"] < -BASAL_RISE and share >= BASAL_SHARE
    return {"evaluable": evaluable, "rising": up, "falling": down,
            "near_miss_high": v["near_misses"] >= BASAL_NEAR_MISSES, "values": v}


def step_window(window: list[dict], baseline: list[dict], readings: list[dict]) -> dict:
    v, _ = N.step_window_metrics(window, baseline, [], [], window_readings=readings, window_days=STEP_DAYS)
    return {
        "baseline_ok": v["baseline_nights"] >= BASELINE_MIN,
        "insufficient": v["insufficient"],
        "shift": v["low_point_shift"] is not None and v["low_point_shift"] <= SHIFT,
        "tbr": v["tbr_pct"] is not None and v["tbr_pct"] > TBR,
        "near_misses": v["near_misses"] >= STEP_NEAR_MISSES,
        "highs": (v["ketone_risk_episodes"] or 0) >= KETONE_EPISODES,
        "values": v,
    }


# ---------------------------------------------------------------- 1. lead time

def lead_time(recs: list[dict], change: Change, previous: date | None) -> dict:
    """Run Basal Check every morning over the prior history (from 90 days back,
    never before the previous change + 30 days) up to the change. Lead = change
    date minus the FIRST morning the rising direction fires."""
    lo = change.day - timedelta(days=LEAD_LOOKBACK_DAYS)
    if previous is not None:
        lo = max(lo, previous + timedelta(days=STABLE_MARGIN_DAYS))
    fired, evaluable = [], []
    d = lo
    while d <= change.day:
        bc = basal_check(nights_in(recs, d - timedelta(days=BASAL_NIGHTS), d))
        if bc["evaluable"]:
            evaluable.append(d)
        if bc["rising"]:
            fired.append(d)
        d += timedelta(days=1)
    first = fired[0] if fired else None
    # censored: the first flag is (nearly) the first morning the rule could run at
    # all, so the true lead may be longer (data start, a CGM gap, or the lookback)
    censored = bool(first and evaluable and (first - evaluable[0]).days <= 3)
    last14 = [e for e in evaluable if e > change.day - timedelta(days=14)]
    return {"first": first, "lead_days": (change.day - first).days if first else None, "censored": censored,
            "fire_mornings": len(fired), "evaluable_mornings": len(evaluable),
            "first_evaluable": evaluable[0] if evaluable else None,
            "last14_share": (sum(f in set(fired) for f in last14) / len(last14)) if last14 else None, "from": lo}


# ---------------------------------------------------------------- 3. detection lag

def detection_lag(recs: list[dict], change: Change) -> dict:
    """Baseline = the 14 nights before the change; each morning after it, the
    last 5 nights (all after the change) against that baseline; lag = days
    until |low-point shift| >= 15 first."""
    base = [r for r in nights_in(recs, change.day - timedelta(days=BASELINE_NIGHTS), change.day) if not r["stale"]]
    if len(base) < BASELINE_MIN:
        return {"lag_days": None, "why": f"baseline has {len(base)} adequate nights"}
    for k in range(STEP_DAYS, DETECT_MAX_DAYS + 1):
        d = change.day + timedelta(days=k)
        win = [r for r in nights_in(recs, d - timedelta(days=STEP_DAYS), d) if not r["stale"]]
        if len(win) < 3:
            continue
        v, _ = N.step_window_metrics(win, base, [], [])
        s = v["low_point_shift"]
        if s is not None and abs(s) >= DETECT_SHIFT:
            return {"lag_days": k, "shift": s, "why": ""}
    return {"lag_days": None, "why": f"no |shift| >= {DETECT_SHIFT:.0f} within {DETECT_MAX_DAYS} days"}


# ---------------------------------------------------------------- 2. firing rates over stable stretches

def near_change(lo: date, hi: date, changes: list[Change]) -> bool:
    return any(lo < c.day + timedelta(days=STABLE_MARGIN_DAYS) and hi > c.day - timedelta(days=STABLE_MARGIN_DAYS)
               for c in changes)


def day_readings(ts: np.ndarray, x: np.ndarray, lo: date, hi: date) -> list[dict]:
    a = np.searchsorted(ts, np.datetime64(datetime(lo.year, lo.month, lo.day), "s"))
    b = np.searchsorted(ts, np.datetime64(datetime(hi.year, hi.month, hi.day), "s"))
    return [{"timestamp": pd.Timestamp(t).to_pydatetime(), "glucose_mgdl": float(v)} for t, v in zip(ts[a:b], x[a:b])]


def firing_rates(recs, lows, ts, x, changes, split: date) -> dict:
    first, last = recs[0]["night_date"], recs[-1]["night_date"]
    out = {"fortnights": [], "step": []}
    unfelt_by_night = lows.groupby("night_date").inferred_unfelt.sum() if len(lows) else pd.Series(dtype=float)

    d = first
    while d + timedelta(days=BASAL_NIGHTS) <= last + timedelta(days=1):
        lo, hi = d, d + timedelta(days=BASAL_NIGHTS)
        d = hi
        if near_change(lo, hi, changes):
            continue
        win = nights_in(recs, lo, hi)
        if len(win) < BASAL_NIGHTS // 2:                      # mostly no data (a CGM gap): not a window
            continue
        bc = basal_check(win)
        unfelt = sum(int(unfelt_by_night.get(str(r["night_date"]), 0)) for r in win)
        out["fortnights"].append({**{k: bc[k] for k in ("evaluable", "rising", "falling", "near_miss_high")},
                                  "unfelt_proxy": unfelt >= 1, "out_of_sample": lo >= split})

    d = first + timedelta(days=BASELINE_NIGHTS)
    while d + timedelta(days=STEP_DAYS) <= last + timedelta(days=1):
        lo, hi = d, d + timedelta(days=STEP_DAYS)
        d = hi
        if near_change(lo - timedelta(days=BASELINE_NIGHTS), hi, changes):
            continue
        base = [r for r in nights_in(recs, lo - timedelta(days=BASELINE_NIGHTS), lo) if not r["stale"]]
        win = nights_in(recs, lo, hi)
        if not win:
            continue
        sw = step_window(win, base, day_readings(ts, x, lo, hi))
        out["step"].append({**{k: sw[k] for k in ("baseline_ok", "insufficient", "shift", "tbr", "near_misses", "highs")},
                            "out_of_sample": lo >= split})
    return out


def rate(rows: list[dict], key: str, gate) -> str:
    ok = [r for r in rows if gate(r)]
    if not ok:
        return "   -   (0 windows)"
    k = sum(bool(r[key]) for r in ok)
    return f"{k:3d} / {len(ok):3d} = {100 * k / len(ok):5.1f}%"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", default="ml/data")
    args = p.parse_args()
    data = Path(args.data)
    from ml.train import holdout_start

    changes = read_changes(data / "therapy_changes.txt")
    recs = load_records(data / "nights_labeled.csv")
    lows = pd.read_csv(data / "lows_labeled.csv")
    clean = pd.read_csv(data / "clean.csv", parse_dates=["timestamp"]).sort_values("timestamp", ignore_index=True)
    ts_all = clean.timestamp.to_numpy("datetime64[s]")
    keep = drop_collisions(ts_all.astype(np.int64))
    ts, x = ts_all[keep], clean.mgdl.to_numpy(float)[keep]
    split = holdout_start(clean.timestamp).date()

    print(f"THERAPY CHANGES: {len(changes)} ({sum(c.confirmed for c in changes)} confirmed by Chris,"
          f" {sum(not c.confirmed for c in changes)} inferred from the data)")
    print("\n1. BASAL CHECK LEAD TIME (rising direction; run every morning over the prior history)")
    print("  change      source     first flag   lead               flagged / evaluable mornings   last 14 mornings flagged   first evaluable")
    prev = None
    for c in changes:
        r = lead_time(recs, c, prev)
        lead = "not flagged" if r["lead_days"] is None else \
            f"{'>=' if r['censored'] else '  '}{r['lead_days']:3d} d ({r['lead_days'] / 7:.1f} wk)"
        share = "-" if r["last14_share"] is None else f"{100 * r['last14_share']:.0f}%"
        print(f"  {c.day}  {'confirmed' if c.confirmed else 'INFERRED '}  {str(r['first'] or '-'):10}   {lead:18}"
              f" {r['fire_mornings']:3d} / {r['evaluable_mornings']:3d} ({100 * r['fire_mornings'] / max(1, r['evaluable_mornings']):3.0f}%)"
              f"            {share:>4}                    {r['first_evaluable']}")
        prev = c.day
    print("  '>=' = the first flag came within 3 days of the first morning the rule could run (data start, a CGM"
          "\n  gap, or the lookback), so the true lead may be longer. Compare the flagged share with the stable-stretch"
          "\n  rate in section 2: a lead only means something when the rule fires far more often before a change.")

    print(f"\n3. DETECTION LAG (days after the change until |low-point shift| >= {DETECT_SHIFT:.0f};"
          f" 5-night window vs the 14 nights before)")
    for c in changes:
        r = detection_lag(recs, c)
        what = f"{r['lag_days']:3d} days (shift {r['shift']:+.0f} mg/dL)" if r["lag_days"] is not None else f"none: {r['why']}"
        print(f"  {c.day}  {'confirmed' if c.confirmed else 'INFERRED '}  {what}")

    fr = firing_rates(recs, lows, ts, x, changes, split)
    fn, st = fr["fortnights"], fr["step"]
    print(f"\n2. FIRING RATES over stable stretches (windows > {STABLE_MARGIN_DAYS} days from every listed change)")
    print(f"  Standing Cards: {len(fn)} non-overlapping 14-night windows")
    bc_ok = lambda r: r["evaluable"]                                 # noqa: E731
    print(f"    Basal Check, rising  (> +{BASAL_RISE:.0f}, >= 70%):   {rate(fn, 'rising', bc_ok)}   (of evaluable: >= 5 clean nights)")
    print(f"    Basal Check, falling (< -{BASAL_RISE:.0f}, >= 70%):   {rate(fn, 'falling', bc_ok)}")
    print(f"    Basal Check, too high (>= {BASAL_NEAR_MISSES} near-misses):   {rate(fn, 'near_miss_high', lambda r: True)}"
          f"   out-of-sample only: {rate(fn, 'near_miss_high', lambda r: r['out_of_sample'])}")
    print(f"    Hypo Response, glucose-side proxy (>= 1 inferred-unfelt low): {rate(fn, 'unfelt_proxy', lambda r: True)}")
    print("    Hypo Response escalations, re-arms, ack times, reported unfelt: NOT computable on history (no acks, no answers)")
    ev = lambda r: r["baseline_ok"] and r["insufficient"] is False   # noqa: E731
    print(f"  Step Watch amber rules: {len(st)} non-overlapping 5-day windows"
          f" ({sum(ev(r) for r in st)} evaluable: baseline >= {BASELINE_MIN} nights, coverage >= 70%)")
    print(f"    low-point shift <= {SHIFT:.0f}:        {rate(st, 'shift', ev)}")
    print(f"    TBR > {TBR:.1f}%:                  {rate(st, 'tbr', ev)}")
    print(f"    near-misses >= {STEP_NEAR_MISSES}:            {rate(st, 'near_misses', ev)}"
          f"   out-of-sample only: {rate(st, 'near_misses', lambda r: ev(r) and r['out_of_sample'])}")
    print(f"    highs, >= {KETONE_EPISODES} ketone-risk episodes: {rate(st, 'highs', ev)}")
    print("    tolerance (stomach check-ins), awareness (morning answers): NOT computable on history")
    print(f"\nnear-miss rows before {split} use forecast_v1, which trained on those nights (leakage guard, step 6.4);"
          "\nthe out-of-sample column is the evidence. n = 1, observational, retrospective.")


if __name__ == "__main__":
    main()
