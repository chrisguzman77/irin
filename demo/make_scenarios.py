"""George, docs/plans/george.md step 7: writes the demo scenarios and their
companion JSON (schema: demo/scenarios/README.md) into demo/scenarios/.
Date-shifted real scenarios (basal-change windows, the core six) need the raw
data on George's laptop and come in later steps; the SYNTHETIC titration
scenario needs nothing and is fully deterministic. Prints summaries only.

titration_synthetic (step 7.3): 49 SYNTHETIC days from 2020-01-01 (obviously
fake): a 14-night baseline, tirzepatide step 1 (2.5 mg, 4 weeks), then step 2
(5 mg) through days 3-7, with the plan, symptom checks, weekly injection logs,
one recall answer, and an alarm-event overlay. It is built so that
ml/models/nights.py reproduces the chris.md R10 worked example EXACTLY over
step 2 days 3-7 against the baseline:
  coverage 1,390 of 1,440 = 96.5%; low point 98 -> 76 (shift -22); TBR 58 of
  1,440 = 4.03%; 2 near-misses; 1 nocturnal low answered don't remember;
  rough stomach on 3 of 5 days;
and step 1's windows stay green. ml/tests/test_scenarios.py locks every one of
these numbers. Insulin sensitivity drifts down faster than the insulin was
reduced at step 2, so the step 2 nights run lower and afternoons dip under 70."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

T0 = datetime(2020, 1, 1)          # SYNTHETIC epoch: obviously fake
STEP_DAYS = 28
BASELINE_NIGHTS = 14
STEP1_DAY = 14                     # plan start (step 1, 2.5 mg)
STEP2_DAY = STEP1_DAY + STEP_DAYS  # 42 (step 2, 5 mg): day 1 of step 2
WINDOW = range(STEP2_DAY + 2, STEP2_DAY + 7)     # step 2 days 3-7 = days 44..48
END_DAY = STEP2_DAY + 7            # data runs to the morning after day 48's night
LADDER = ["2.5 mg", "5 mg", "7.5 mg", "10 mg", "12.5 mg", "15 mg"]

# Night low points (min of the 15-min rolling median), by night index (evening day).
BASELINE_LOWS = [97, 101, 94, 98, 104, 96, 98, 100, 95, 98, 102, 97, 99, 98]   # median 98
STEP1_LOW = [93, 95, 92, 96, 94, 91, 95]                                       # cycles; step 1 stays green
STEP2_LOWS = {42: 84, 43: 80, 44: 78, 45: 62, 46: 76, 47: 76, 48: 76}           # any 5 in a row -> median 76
NEAR_MISS_NIGHTS = {44: 78, 47: 76}           # the two near-miss dips are those nights' troughs
LOW_NIGHT = 45                                # the one nocturnal low (answered don't remember)
AFTERNOON_LOWS = {44: 10, 45: 10, 46: 10, 47: 10, 48: 9}    # readings under 70; + 9 in the night low = 58
DROPOUTS = 50                                  # 1,440 - 50 = 1,390 present in the window
ROUGH_DAYS = {44, 46, 47}                      # rough 3 of 5


def _t(day: int, h: int, m: int = 0) -> datetime:
    return T0 + timedelta(days=day, hours=h, minutes=m)


def _slots(day0: int, day1: int) -> list[datetime]:
    """5-min grid from day0 00:00 to day1 10:00."""
    n = int(((day1 - day0) * 24 + 10) * 12)
    return [_t(day0, 0) + timedelta(minutes=5 * k) for k in range(n)]


def _meal(hours: np.ndarray, at: float, peak: float) -> np.ndarray:
    """A meal bump: rise over ~50 min, decay over ~3 h."""
    dt = np.clip(hours - at, 0.0, None)
    return peak * (dt / 0.8) * np.exp(1 - dt / 0.8)


def titration() -> tuple[list[dict], dict]:
    rng = np.random.default_rng(20200101)
    ts = _slots(0, END_DAY)
    hours = np.array([(t - T0).total_seconds() / 3600 for t in ts])
    hod = hours % 24
    day = (hours // 24).astype(int)

    g = np.full(len(ts), 118.0)
    for d in range(END_DAY + 1):
        h = hours - 24 * d
        g += _meal(h, 7.75, 52) + _meal(h, 12.5, 55) + _meal(h, 18.75, 58)
    g += rng.normal(0, 2.0, len(ts))
    g = np.clip(g, 85, 185)

    def night_index(i: int) -> int | None:
        if hod[i] >= 22:
            return int(day[i])
        if hod[i] < 7:
            return int(day[i]) - 1
        return None

    def low_for(n: int) -> int:
        if n < BASELINE_NIGHTS:
            return BASELINE_LOWS[n]
        if n < STEP2_DAY:
            return STEP1_LOW[(n - BASELINE_NIGHTS) % len(STEP1_LOW)]
        return STEP2_LOWS.get(n, 80)

    # Every night: keep readings well above its trough, then carve the trough near 03:00.
    idx = {t: k for k, t in enumerate(ts)}
    for n in range(-1, END_DAY):
        target = low_for(max(n, 0))
        night = [k for k in range(len(ts)) if night_index(k) == n]
        if not night:
            continue
        for k in night:
            g[k] = max(g[k], target + 12 + abs(rng.normal(0, 3)))
        if n == LOW_NIGHT:
            # a slow, untreated, unfelt-looking low: under 70 for 7 readings, recovery < 1 mg/dL/min
            shape = [96, 88, 80, 74, 68, 64, 62, 62, 62, 64, 66, 67, 69, 72, 74, 76, 78, 80, 83, 86, 90, 94]
            start = idx[_t(n + 1, 2, 30)]
        else:
            shape = [target + 12, target + 8, target + 4, target, target, target, target + 4, target + 8, target + 12]
            start = idx[_t(n + 1, 2, 45)]
        for j, v in enumerate(shape):
            g[start + j] = v
        if n == LOW_NIGHT:
            # no rebound of more than 60 above the nadir within 2 h: an untreated low, not a treated one
            for k in range(start + len(shape), start + 36):
                g[k] = min(g[k], 110 + abs(rng.normal(0, 2)))

    # Afternoon dips under 70 in the step 2 window (daytime: never a nocturnal low event).
    for d, n_low in AFTERNOON_LOWS.items():
        s = idx[_t(d, 14, 30)]
        ramp_in, ramp_out = [140, 126, 112, 98, 86, 76], [74, 82, 94, 108, 122]
        seq = ramp_in + [66 - (j % 3) for j in range(n_low)] + ramp_out
        for j, v in enumerate(seq):
            g[s + j] = v

    g = np.round(g).astype(int)

    # Dropouts: 50 single missing readings in the window days, never a trough, a dip, or a low.
    protected = set()
    for k in range(len(ts)):
        if g[k] < 90 or night_index(k) in NEAR_MISS_NIGHTS or night_index(k) == LOW_NIGHT and 1 <= hod[k] < 5:
            protected.update({k - 1, k, k + 1})
    window_idx = [k for k in range(len(ts)) if day[k] in WINDOW and k not in protected]
    pick = sorted(rng.choice(window_idx[::4], size=DROPOUTS, replace=False))
    keep = np.ones(len(ts), bool)
    keep[pick] = False

    rows = []
    prev = None
    for k in np.flatnonzero(keep):
        v = int(g[k])
        rows.append({"timestamp": ts[k].isoformat(), "glucose_mgdl": v, "trend": _trend(prev, (ts[k], v))})
        prev = (ts[k], v)

    companion = _titration_companion(rows)
    return rows, companion


def _trend(prev, cur) -> str:
    """Nightscout direction names from the 5-min rate (mg/dL per min)."""
    if prev is None:
        return "Flat"
    rate = (cur[1] - prev[1]) / max(1.0, (cur[0] - prev[0]).total_seconds() / 60)
    for limit, up, down in ((3, "DoubleUp", "DoubleDown"), (2, "SingleUp", "SingleDown"), (1, "FortyFiveUp", "FortyFiveDown")):
        if rate >= limit:
            return up
        if rate <= -limit:
            return down
    return "Flat"


def _titration_companion(rows: list[dict]) -> dict:
    from ml.models import nights as N

    readings = [{"timestamp": datetime.fromisoformat(r["timestamp"]), "glucose_mgdl": float(r["glucose_mgdl"])} for r in rows]
    plan = {
        "plan_id": "demo-tirzepatide",
        "drug_class": "gip_glp1",
        "drug_label": "tirzepatide",
        "steps": [{"index": i, "dose_label": lab, "planned_start": (T0 + timedelta(days=STEP1_DAY + i * STEP_DAYS)).date().isoformat()}
                  for i, lab in enumerate(LADDER)],
        "started_at": (T0 + timedelta(days=STEP1_DAY)).date().isoformat(),
        "on_insulin": True,
        "options": {"ketone_prompts": True, "step_week_vigilance": False, "vigilance_offset_mgdl": 10.0},
        "status": "active",
        "is_demo": True,
    }
    injections = [{"timestamp": _t(d, 9).isoformat(), "kind": "glp1_dose", "dose_label": LADDER[0 if d < STEP2_DAY else 1],
                   "confirmed": True} for d in range(STEP1_DAY, END_DAY + 1, 7)]
    checks = [{"date": (T0 + timedelta(days=d)).date().isoformat(),
               "gi": "rough" if d in ROUGH_DAYS else "fine", "is_demo": True}
              for d in range(STEP1_DAY, END_DAY)]

    alarm_events = []
    for n, trough in NEAR_MISS_NIGHTS.items():                 # predicted-low warnings that never crossed
        st = _t(n + 1, 2, 30)
        alarm_events.append({"event_id": f"demo-nm-{n}", "tier": "predicted_low", "started_at": st.isoformat(),
                             "acknowledged_at": (st + timedelta(minutes=3)).isoformat(), "ack_source": "device",
                             "escalated": False, "rearm_count": 0, "crossed_actual": False,
                             "presence_during": "home", "is_demo": True})
    warn = _t(LOW_NIGHT + 1, 2, 20)                             # the low: warned, then crossed; acked half-asleep
    alarm_events.append({"event_id": f"demo-warn-{LOW_NIGHT}", "tier": "predicted_low", "started_at": warn.isoformat(),
                         "acknowledged_at": None, "ack_source": None, "escalated": True, "rearm_count": 0,
                         "crossed_actual": True, "presence_during": "home", "is_demo": True})
    lows = N.low_events(readings, None, [], (_t(LOW_NIGHT, 22), _t(LOW_NIGHT + 1, 7)))
    crossing = lows[0]["started_at"]
    alarm_events.append({"event_id": f"demo-low-{LOW_NIGHT}", "tier": "actual_low", "started_at": crossing.isoformat(),
                         "acknowledged_at": (crossing + timedelta(minutes=6)).isoformat(), "ack_source": "device",
                         "escalated": True, "rearm_count": 0, "crossed_actual": True,
                         "presence_during": "home", "is_demo": True})

    codes = {}
    for n in range(END_DAY):
        c, src = N.classify_night(readings, None, None, None, (_t(n, 22), _t(n + 1, 7)))
        codes[(T0 + timedelta(days=n)).date().isoformat()] = {"codes": c, "code_source": src}

    return {
        "scenario": "titration_synthetic",
        "kind": "titration",
        "synthetic": True,
        "overlay_synthetic": True,
        "night_window": {"start": "22:00", "end": "07:00"},
        "reason_codes": codes,
        "plan": plan,
        "dose_change": None,
        "symptom_checks": checks,
        "injections": injections,
        "recall_answers": {lows[0]["low_event_id"]: "dont_remember"},
        "alarm_events": sorted(alarm_events, key=lambda e: e["started_at"]),
    }


def write(out: Path, name: str, rows: list[dict], companion: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with (out / f"{name}.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["timestamp", "glucose_mgdl", "trend"], lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    (out / f"{name}.json").write_text(json.dumps(companion, indent=2) + "\n", newline="\n")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", default="demo/scenarios")
    p.add_argument("--only", choices=["titration"], default="titration")
    args = p.parse_args()
    out = Path(args.out)
    rows, comp = titration()
    write(out, "titration_synthetic", rows, comp)
    print(f"titration_synthetic (SYNTHETIC): {len(rows)} readings, {len(comp['reason_codes'])} nights,"
          f" {len(comp['alarm_events'])} alarm events, {len(comp['symptom_checks'])} symptom checks,"
          f" {len(comp['injections'])} injections -> {out}")


if __name__ == "__main__":
    main()
