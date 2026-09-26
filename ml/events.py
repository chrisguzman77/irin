"""george.md step 3.4: the reusable event replay. Runs a forecaster over the
readings through the ACTUAL alarm rule (backend/app/alarm.py, warning tier)
and scores it on events: detection rate (warning >= 10 min before crossing
70), median lead time, false alarms per night. Step 5 runs the same thing over
the whole history.

A forecaster is a function X (N, 16 features, FEATURE_NAMES order) -> the
predicted glucose 30 min ahead (absolute mg/dL). Baselines and the trained
model share that shape, so dataset.csv point metrics and this replay run the
same code.

The rule, as alarm.py states it:
  - a warning starts on N = 2 consecutive forecasts below the threshold;
  - it clears on 2 consecutive forecasts back at or above the threshold;
  - it escalates (ends as a true warning) when the actual value crosses 70;
  - no warning starts during an actual low (actual_low outranks predicted_low).
A reading with no usable 60-min slot window (features.slot_windows) has no forecast:
it breaks both consecutive counts and leaves the warning state unchanged.

Low event: the first reading under 70 (a single reading counts, George's
call); a NEW event needs 2 consecutive readings back at or above 70.
Scoring per low event, by the lead = crossing time minus the start of the
warning that is on at the crossing:
  detected  10 <= lead <= 60 min
  late      lead < 10 min
  long      lead > 60 min (warned, but a warning that has sat on for over an
            hour is not credited; kept out of detection and lead statistics)
  missed    no warning on at the crossing
Every warning episode that clears without a crossing is a false alarm, split
by the lowest actual reading between its start and its clear: near (under 80,
glucose did come close) or far (80 and up).
A low is "forecastable" when at least 2 forecasts exist between 40 and 10 min
before the crossing, both ends inclusive: the fewest a forecaster needs to
fire the N = 2 rule with the minimum lead. The rest (after gaps, sensor
warm-up) are reported separately so no forecaster is blamed for missing data."""

from __future__ import annotations

import argparse
from typing import Callable

import numpy as np

from ml.models.features import _hours, features, grid_slots, slot_windows

LOW = 70.0
MIN_LEAD_MIN = 10.0
MAX_LEAD_MIN = 60.0
NEAR_MISS_MGDL = 80.0
FCST_FROM_MIN, FCST_TO_MIN = 40, 10   # forecastable: >= 2 forecasts in [crossing - 40, crossing - 10]
NIGHT = ("22:00", "07:00")
READINGS_PER_DAY = 288
READINGS_PER_NIGHT = 108           # 9 h x 12


def in_night(hours: np.ndarray, night_window: tuple[str, str] = NIGHT) -> np.ndarray:
    start, end = _hours(night_window[0]), _hours(night_window[1])
    h = np.asarray(hours, dtype=float)
    return (h >= start) | (h < end) if start > end else (h >= start) & (h < end)


def local_hours(ts: np.ndarray) -> np.ndarray:
    """Fractional local hour of naive datetime64 timestamps."""
    t = np.asarray(ts, dtype="datetime64[s]")
    return (t - t.astype("datetime64[D]")).astype(np.int64) / 3600.0


def forecasts(ts: np.ndarray, mgdl: np.ndarray, forecast: Callable[[np.ndarray], np.ndarray]) -> np.ndarray:
    """Predicted value 30 min ahead at every reading; NaN where the reading has
    no usable slot window (features.slot_windows, the rule the Pi applies) or
    the forecaster itself returns NaN (a baseline missing the lag it needs)."""
    t = np.asarray(ts, dtype="datetime64[s]")
    x = np.asarray(mgdl, dtype=float)
    out = np.full(len(x), np.nan)
    keep, gslot, stretch = grid_slots(t.astype(np.int64))
    if not len(gslot):
        return out
    windows, ok = slot_windows(gslot, stretch, x[keep])
    idx = np.flatnonzero(keep)[ok]
    if len(idx):
        out[idx] = forecast(features(windows[ok], local_hours(t[idx])))
    return out


def replay_events(ts: np.ndarray, mgdl: np.ndarray, preds: np.ndarray,
                  threshold: float = 70.0, n_consecutive: int = 2) -> dict:
    """Walk the readings in time order through the warning rule. preds is
    aligned with the readings (NaN = no forecast). Returns the raw lows and
    warnings; summarize() turns a time range of them into metrics."""
    t = np.asarray(ts, dtype="datetime64[s]")
    x = np.asarray(mgdl, dtype=float)
    p = np.asarray(preds, dtype=float)
    night = in_night(local_hours(t))
    lows: list[dict] = []
    warnings: list[dict] = []

    in_low, above = False, 0
    warn: dict | None = None
    below = recov = 0
    for i in range(len(x)):
        if not in_low and x[i] < LOW:
            in_low, above = True, 0
            low = {"i": i, "time": t[i], "night": bool(night[i]), "outcome": "missed", "lead_min": None}
            if warn is not None:
                lead = (t[i] - warn["time"]).astype(np.int64) / 60
                low["lead_min"] = float(lead)
                low["outcome"] = ("late" if lead < MIN_LEAD_MIN else
                                  "long" if lead > MAX_LEAD_MIN else "detected")
                warn.update(end="escalated", end_i=i, lead_min=float(lead))
                warn = None
            lows.append(low)
            below = recov = 0
            continue
        if in_low:
            above = above + 1 if x[i] >= LOW else 0
            if above >= 2:
                in_low = False
            below = 0
            continue

        if np.isnan(p[i]):
            below = recov = 0
        elif warn is not None:
            recov = recov + 1 if p[i] >= threshold else 0
            if recov >= 2:
                warn.update(end="cleared", end_i=i, nadir=float(x[warn["i"]:i + 1].min()))
                warn, recov = None, 0
        elif p[i] < threshold:
            below += 1
            if below >= n_consecutive:
                warn = {"i": i, "time": t[i], "night": bool(night[i]), "end": "open", "end_i": None, "lead_min": None, "nadir": None}
                warnings.append(warn)
                below = recov = 0
        else:
            below = 0

    # forecastable: >= 2 forecasts in the 40..10 min before the crossing
    have = np.flatnonzero(~np.isnan(p))
    have_t = t[have]
    for low in lows:
        c = low["time"]
        lo = np.searchsorted(have_t, c - np.timedelta64(FCST_FROM_MIN * 60, "s"), "left")
        hi = np.searchsorted(have_t, c - np.timedelta64(FCST_TO_MIN * 60, "s"), "right")
        low["forecastable"] = bool(hi - lo >= 2)
    return {"ts": t, "night": night, "lows": lows, "warnings": warnings, "threshold": threshold}


def summarize(rep: dict, start=None, end=None) -> dict:
    """Metrics over lows/warnings whose time falls in [start, end). Denominators
    are sensor coverage in that range: readings / 288 = days, night-window
    readings / 108 = nights."""
    t = rep["ts"]
    lo = t[0] if start is None else np.datetime64(start, "s")
    hi = t[-1] + np.timedelta64(1, "s") if end is None else np.datetime64(end, "s")
    in_rng = (t >= lo) & (t < hi)
    days = in_rng.sum() / READINGS_PER_DAY
    nights = (in_rng & rep["night"]).sum() / READINGS_PER_NIGHT

    def pick(items):
        return [e for e in items if lo <= e["time"] < hi]

    lows, warns = pick(rep["lows"]), pick(rep["warnings"])
    out = {"days": float(days), "nights": float(nights)}
    for scope, keep in (("all", lambda e: True), ("night", lambda e: e["night"])):
        L = [e for e in lows if keep(e)]
        F = [e for e in L if e["forecastable"]]
        det = [e for e in L if e["outcome"] == "detected"]
        det_f = [e for e in F if e["outcome"] == "detected"]
        fa = [w for w in warns if keep(w) and w["end"] == "cleared"]
        leads = [e["lead_min"] for e in det]
        denom = days if scope == "all" else nights
        out[scope] = {
            "lows": len(L),
            "forecastable": len(F),
            "detected": len(det),
            "late": sum(e["outcome"] == "late" for e in L),
            "long": sum(e["outcome"] == "long" for e in L),
            "missed": sum(e["outcome"] == "missed" for e in L),
            "detection": len(det) / len(L) if L else None,
            "detection_forecastable": len(det_f) / len(F) if F else None,
            "median_lead_min": float(np.median(leads)) if leads else None,
            "p90_lead_min": float(np.percentile(leads, 90)) if leads else None,
            "warnings": sum(keep(w) for w in warns),
            "false_alarms": len(fa),
            "false_near": sum(w["nadir"] < NEAR_MISS_MGDL for w in fa),
            "false_far": sum(w["nadir"] >= NEAR_MISS_MGDL for w in fa),
            "false_per_unit": len(fa) / denom if denom else None,     # per day (all) / per night (night)
            "false_far_per_unit": sum(w["nadir"] >= NEAR_MISS_MGDL for w in fa) / denom if denom else None,
        }
    return out


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()
    print("library module: run `python -m ml.evaluate` for the step 3 tables")


if __name__ == "__main__":
    main()
