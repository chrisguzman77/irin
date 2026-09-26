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


# ---------------------------------------------------------------- 7b: basal-change windows (real, date-shifted)

TARGET_CHANGE = date(2021, 2, 1)   # where a change lands after the shift; the real date and the offset never enter the repo
SIDE_NIGHTS = 14
EXTRA_BEFORE = 7                   # a week more before, so every morning of the last week has a full 14-night Basal Check window
RETRAIN_PAD = (timedelta(minutes=35), timedelta(minutes=65))   # no training row whose label or window touches the scenario


def _confirmed_change(n: int, data: Path) -> tuple[date, float | None]:
    """The n-th CONFIRMED change in ml/data/therapy_changes.txt (gitignored)."""
    import re

    rows = []
    for line in (data / "therapy_changes.txt").read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split()
        if parts[2].upper().startswith("INFERRED"):
            continue
        m = re.search(r"units=(\d+(?:\.\d+)?)", line)
        rows.append((date.fromisoformat(parts[0]), float(m.group(1)) if m else None))
    return sorted(rows)[n - 1]


def basal_change(n: int, data: Path) -> tuple[list[dict], dict, dict]:
    """14 nights before and 14 after confirmed change n, date-shifted so the
    change lands on TARGET_CHANGE. Alarm events come from a forecaster
    RETRAINED WITHOUT this window (george.md step 6.4), replayed through the
    warning rule at 85; their response side (acks, escalation, presence) and
    the recall answers are a labeled SYNTHETIC overlay."""
    import pandas as pd
    import xgboost  # noqa: F401  (fail early if missing)

    from ml.events import forecasts, replay_events
    from ml.label_history import alarm_events_from_replay
    from ml.models import nights as N
    from ml.models.features import drop_collisions
    from ml.models.predict import PREDICTED_LOW_THRESHOLD
    from ml.train import fit, forecaster

    change, units = _confirmed_change(n, data)
    c0 = datetime(change.year, change.month, change.day)
    lo = c0 - timedelta(days=SIDE_NIGHTS + EXTRA_BEFORE) + timedelta(hours=12)   # noon before the first night
    hi = c0 + timedelta(days=SIDE_NIGHTS, hours=10)                    # 10:00 after the last night
    shift = datetime(TARGET_CHANGE.year, TARGET_CHANGE.month, TARGET_CHANGE.day) - c0

    clean = pd.read_csv(data / "clean.csv", parse_dates=["timestamp"]).sort_values("timestamp", ignore_index=True)
    ts_all = clean.timestamp.to_numpy("datetime64[s]")
    keep = drop_collisions(ts_all.astype(np.int64))
    ts, x = ts_all[keep], clean.mgdl.to_numpy(float)[keep]

    rows_ds = pd.read_csv(data / "dataset.csv", parse_dates=["timestamp"])
    excl = (rows_ds.timestamp >= lo - RETRAIN_PAD[0]) & (rows_ds.timestamp <= hi + RETRAIN_PAD[1])
    booster, info = fit(rows_ds[~excl])
    preds = forecasts(ts, x, forecaster(booster))

    a, b = np.searchsorted(ts, np.datetime64(lo, "s")), np.searchsorted(ts, np.datetime64(hi, "s"))
    t_win, x_win, p_win = ts[a:b], x[a:b], preds[a:b]
    rep_ = replay_events(t_win, x_win, p_win, threshold=PREDICTED_LOW_THRESHOLD)

    shifted = [pd.Timestamp(t).to_pydatetime() + shift for t in t_win]
    rows, prev = [], None
    for t, v in zip(shifted, x_win):
        v = int(round(v))
        rows.append({"timestamp": t.isoformat(), "glucose_mgdl": v, "trend": _trend(prev, (t, v))})
        prev = (t, v)
    readings = [{"timestamp": t, "glucose_mgdl": float(v)} for t, v in zip(shifted, x_win)]

    rng = np.random.default_rng(n)
    alarms = []
    for e in alarm_events_from_replay(rep_):
        st = e["started_at"] + shift
        delay = float(rng.choice([1.5, 2, 3, 4, 7]))           # overlay: minutes to acknowledge
        alarms.append({"event_id": f"demo-bc{n}-{e['tier'][:4]}-{st:%Y%m%dT%H%M%S}", "tier": e["tier"],
                       "started_at": st.isoformat(),
                       "acknowledged_at": (st + timedelta(minutes=delay)).isoformat(),
                       "ack_source": "device" if rng.random() < 0.8 else "app",
                       "escalated": delay > 5, "rearm_count": 0, "crossed_actual": e["crossed_actual"],
                       "presence_during": "home", "is_demo": True})

    first_night = TARGET_CHANGE - timedelta(days=SIDE_NIGHTS + EXTRA_BEFORE)
    codes, lows = {}, []
    for k in range(2 * SIDE_NIGHTS + EXTRA_BEFORE):
        d = first_night + timedelta(days=k)
        win = (datetime(d.year, d.month, d.day, 22), datetime(d.year, d.month, d.day, 7) + timedelta(days=1))
        cc, src = N.classify_night(readings, None, None, None, win)
        codes[d.isoformat()] = {"codes": cc, "code_source": src}
        lows += N.low_events(readings, None, [], win)
    answers = ["felt_and_treated", "woke_no_symptoms", "dont_remember"]
    recall = {e["low_event_id"]: (None if k == len(lows) - 1 else answers[k % len(answers)])
              for k, e in enumerate(lows)}                    # the last low is left unanswered: "no answer"

    companion = {
        "scenario": f"basal_change_{n}",
        "kind": "basal_change",
        "synthetic": False,
        "overlay_synthetic": True,
        "night_window": {"start": "22:00", "end": "07:00"},
        "reason_codes": codes,
        "plan": None,
        "dose_change": {"date": TARGET_CHANGE.isoformat(), "insulin": "basal", "new_units": units},
        "symptom_checks": [],
        "injections": [],
        "recall_answers": recall,
        "alarm_events": sorted(alarms, key=lambda e: e["started_at"]),
    }
    summary = {"train_rows": info["rows"], "rounds": info["rounds"], "lows": len(lows),
               "warnings": sum(e["tier"] == "predicted_low" for e in alarms),
               "near_miss_candidates": sum(e["tier"] == "predicted_low" and not e["crossed_actual"] for e in alarms)}
    return rows, companion, summary


# ---------------------------------------------------------------- 7a: the six core scenarios (real, date-shifted)

CORE_TARGET = date(2021, 3, 1)      # core scenario k's night starts on CORE_TARGET + k days (shifted calendar)
CORE_ORDER = ["the_save", "normal_night", "failure", "meal_context", "rearm_low", "high_spike"]
HIGH = 250.0


def _core_candidates(data: Path):
    """Pick one real night per core scenario by stated criteria. Returns
    {name: (start, end, why)} in REAL time (never written to the repo) and the
    shipped model's replay for The Save's check."""
    import pandas as pd
    import xgboost as xgb

    from ml.events import forecasts, replay_events
    from ml.models.features import drop_collisions
    from ml.models.predict import PREDICTED_LOW_THRESHOLD
    from ml.train import MODELS, forecaster, holdout_start

    clean = pd.read_csv(data / "clean.csv", parse_dates=["timestamp"]).sort_values("timestamp", ignore_index=True)
    ts_all = clean.timestamp.to_numpy("datetime64[s]")
    keep = drop_collisions(ts_all.astype(np.int64))
    ts, x = ts_all[keep], clean.mgdl.to_numpy(float)[keep]
    split = holdout_start(clean.timestamp).to_pydatetime()
    booster = xgb.Booster()
    booster.load_model(str(MODELS / "forecast_v1.json"))
    rep_ = replay_events(ts, x, forecasts(ts, x, forecaster(booster)), threshold=PREDICTED_LOW_THRESHOLD)
    nights = pd.read_csv(data / "nights_labeled.csv", parse_dates=["night_date"])
    tsec = ts.astype(np.int64)

    def seg(a: datetime, b: datetime):
        i, j = np.searchsorted(ts, np.datetime64(a, "s")), np.searchsorted(ts, np.datetime64(b, "s"))
        return ts[i:j], x[i:j]

    def night_span(d) -> tuple[datetime, datetime]:
        d = pd.Timestamp(d).to_pydatetime()
        return d.replace(hour=20), d.replace(hour=8) + timedelta(days=1)

    picks = {}
    # the_save: held-out overnight crossing, warned 10-60 min ahead, >= 2 readings under 70; longest lead
    best = None
    for low in rep_["lows"]:
        t = pd.Timestamp(low["time"]).to_pydatetime()
        if t < split or not low["night"] or low["outcome"] != "detected":
            continue
        i = low["i"]
        run = 0
        while i + run < len(x) and x[i + run] < 70:
            run += 1
        if run >= 2 and (best is None or low["lead_min"] > best[1]):
            best = (t, low["lead_min"], run)
    t, lead, run = best
    # Window: at least 1 h of forecaster warm-up plus 15 min before the warning, and no reading at or
    # above the 250 high threshold (a high alert would muddy the two low tiers): start after the last
    # high before the low, end before the first high after it (this real low was over-treated and
    # rebounds past 250), and never later than 2 h after the crossing.
    warn = t - timedelta(minutes=lead)
    a0, b0 = t - timedelta(hours=3), t + timedelta(hours=2)
    tt, xx = seg(a0, b0)
    tpy = [pd.Timestamp(v).to_pydatetime() for v in tt]
    before = [k for k, v in enumerate(tpy) if v < t and xx[k] >= HIGH]
    after = [k for k, v in enumerate(tpy) if v > t and xx[k] >= HIGH]
    start = tpy[before[-1] + 1] if before else a0
    end = tpy[after[0]] if after else b0
    assert warn - start >= timedelta(minutes=75), "not enough warm-up before the warning after trimming the high"
    picks["the_save"] = (start, end,
                         f"held-out overnight low, warned {lead:.0f} min ahead by forecast_v1 (out-of-sample), {run} readings under 70;"
                         f" trimmed to stay under {HIGH:.0f} (real over-treated rebound after it)")

    ok = nights[~nights.stale]
    # normal_night: held-out if possible, clean, no warnings, everything 90-180 from 20:00 to 08:00; lowest spread
    best = None
    for r in ok.itertuples():
        if r.reason_codes != "clean" or r.warnings_inferred:
            continue
        a, b = night_span(r.night_date)
        tt, xx = seg(a, b)
        if len(xx) < 0.9 * 144 or xx.min() < 90 or xx.max() > 180 or np.diff(tt.astype(np.int64)).max() > 600:
            continue
        key = (a >= split, -xx.std())
        if best is None or key > best[0]:
            best = (key, a, b)
    picks["normal_night"] = (best[1], best[2], f"clean night, 90-180 mg/dL all night, no warnings ({'held-out' if best[0][0] else 'in-sample'})")

    # failure: a 40-90 min sensor gap in the middle of the night, glucose never under 80 around it
    best = None
    for r in ok.itertuples():
        a, b = night_span(r.night_date)
        tt, xx = seg(a, b)
        if len(tt) < 2:
            continue
        gaps = np.diff(tt.astype(np.int64)) / 60
        k = int(np.argmax(gaps))
        mid = pd.Timestamp(tt[k]).to_pydatetime()
        if 40 <= gaps[k] <= 90 and 0 <= (mid.hour + 24 - 22) % 24 <= 6 and xx.min() >= 80:
            key = -abs(gaps[k] - 60)
            if best is None or key > best[0]:
                best = (key, a, b, gaps[k])
    picks["failure"] = (best[1], best[2], f"a {best[3]:.0f}-min sensor gap mid-night (the display must go STALE)")

    # meal_context: inferred late meal with a TYPICAL rise (closest to +100 in the first 2 h), under 250, no lows
    best = None
    for r in ok.itertuples():
        if "late_meal" not in r.reason_codes or r.low_events or r.minutes_below_70:
            continue
        a, b = night_span(r.night_date)
        tt, xx = seg(a + timedelta(hours=2), a + timedelta(hours=4))
        if len(xx) < 12:
            continue
        rise = xx.max() - xx[0]
        _, whole = seg(a, b)
        if whole.max() >= HIGH:
            continue
        key = -abs(rise - 100)
        if best is None or key > best[0]:
            best = (key, a, b, rise)
    picks["meal_context"] = (best[1], best[2], f"inferred late meal: +{best[3]:.0f} mg/dL in the first 2 h of the night, peak under {HIGH:.0f}")

    # rearm_low: the longest overnight run under 70 (>= 30 min), no gap inside
    best = None
    below = x < 70
    k = 0
    while k < len(x):
        if below[k]:
            j = k
            while j + 1 < len(x) and below[j + 1] and tsec[j + 1] - tsec[j] <= 600:
                j += 1
            t = pd.Timestamp(ts[k]).to_pydatetime()
            mins = (j - k + 1) * 5
            if (t.hour >= 22 or t.hour < 7) and mins >= 30 and (best is None or mins > best[0]):
                best = (mins, t, pd.Timestamp(ts[j]).to_pydatetime())
            k = j + 1
        else:
            k += 1
    mins, t, t_end = best
    picks["rearm_low"] = (t - timedelta(hours=3), t_end + timedelta(hours=1),
                          f"overnight low under 70 for {mins} min ({'held-out' if t >= split else 'in-sample: its warnings come from a model that trained on it; it demonstrates the re-arm, not forecast accuracy'})")

    # high_spike: a night above 250 for >= 30 min, then back under 200, no gap
    best = None
    for r in ok.itertuples():
        a, b = night_span(r.night_date)
        tt, xx = seg(a, b)
        if len(xx) < 0.9 * 144 or np.diff(tt.astype(np.int64)).max() > 600:
            continue
        over = (xx >= HIGH).sum() * 5
        if over >= 30 and xx[-12:].max() < 200 and xx.max() <= 350:
            key = -abs(over - 60)
            if best is None or key > best[0]:
                best = (key, a, b, over, xx.max())
    picks["high_spike"] = (best[1], best[2], f"above {HIGH:.0f} for {best[3]} min, peak {best[4]:.0f}, back under 200 by morning")
    return picks, ts, x


def core(data: Path) -> dict[str, tuple[list[dict], str, str]]:
    """{name: (rows, why, real window as text for the local printout only)}"""
    import pandas as pd

    picks, ts, x = _core_candidates(data)
    out = {}
    for k, name in enumerate(CORE_ORDER):
        a, b, why = picks[name]
        target = CORE_TARGET + timedelta(days=k)
        shift = datetime(target.year, target.month, target.day) - datetime(a.year, a.month, a.day)   # whole days
        i, j = np.searchsorted(ts, np.datetime64(a, "s")), np.searchsorted(ts, np.datetime64(b, "s"))
        rows, prev = [], None
        for t, v in zip(ts[i:j], x[i:j]):
            t = pd.Timestamp(t).to_pydatetime() + shift
            v = int(round(v))
            rows.append({"timestamp": t.isoformat(), "glucose_mgdl": v, "trend": _trend(prev, (t, v))})
            prev = (t, v)
        out[name] = (rows, why, f"{a:%Y-%m-%d %H:%M} .. {b:%Y-%m-%d %H:%M}")
    return out


def write_csv(out: Path, name: str, rows: list[dict]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with (out / f"{name}.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["timestamp", "glucose_mgdl", "trend"], lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def write(out: Path, name: str, rows: list[dict], companion: dict) -> None:
    write_csv(out, name, rows)
    (out / f"{name}.json").write_text(json.dumps(companion, indent=2) + "\n", newline="\n")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", default="demo/scenarios")
    p.add_argument("--only", choices=["all", "titration", "basal", "core"], default="all")
    p.add_argument("--data", default="ml/data", help="raw-data folder (gitignored); only the real scenarios need it")
    args = p.parse_args()
    out = Path(args.out)
    if args.only in ("all", "titration"):
        rows, comp = titration()
        write(out, "titration_synthetic", rows, comp)
        print(f"titration_synthetic (SYNTHETIC): {len(rows)} readings, {len(comp['reason_codes'])} nights,"
              f" {len(comp['alarm_events'])} alarm events, {len(comp['symptom_checks'])} symptom checks,"
              f" {len(comp['injections'])} injections -> {out}")
    if args.only in ("all", "core"):
        data = Path(args.data)
        if not (data / "nights_labeled.csv").exists():
            print("core scenarios: skipped (the raw data is not here)")
        else:
            print("CORE SCENARIOS (real glucose, date-shifted; the REAL windows below are printed for checkpoint 7"
                  " only and never written to the repo)")
            for name, (rows, why, real) in core(data).items():
                write_csv(out, name, rows)
                print(f"  {name:13} {len(rows):4d} readings  {rows[0]['timestamp'][:16]}..{rows[-1]['timestamp'][11:16]}"
                      f"  real {real}  | {why}")
    if args.only in ("all", "basal"):
        data = Path(args.data)
        if not (data / "therapy_changes.txt").exists():
            print(f"basal_change_1: skipped ({data / 'therapy_changes.txt'} not here: real scenarios build only where the raw data lives)")
            return
        rows, comp, s = basal_change(1, data)
        write(out, "basal_change_1", rows, comp)
        print(f"basal_change_1 (real glucose, date-shifted; overlay SYNTHETIC): {len(rows)} readings,"
              f" {len(comp['reason_codes'])} nights, {s['lows']} nocturnal lows, {s['warnings']} warnings"
              f" ({s['near_miss_candidates']} did not cross); forecaster retrained without the window"
              f" ({s['train_rows']} rows, {s['rounds']} rounds) -> {out}")


if __name__ == "__main__":
    main()
