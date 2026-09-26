"""The ONE night-classification and metrics module (george.md step 4),
imported by the backend's ledger, Standing Cards, and Step Watch AND by the
validation scripts. Pure functions, numpy only, no I/O. Every metric is
computed here and nowhere else. A value that cannot be computed is None,
never zero; window summaries carry their confidence label (measured /
reported / inferred) in a parallel dict.

Inputs are duck-typed: the backend passes its contracts objects (Reading,
Treatment, AlarmEvent, PresenceState, LowEventRecall, SymptomCheck,
NightRecord) and the history scripts pass dicts with the same field names.
Timestamps are datetimes, all naive local time or all timezone-aware, never
mixed. night_window is the concrete (start, end) datetime pair of one night.
Readings may extend past the window (a low near its end needs the 30 min of
recovery after it); each function only counts what the rule says.

Decisions (George and Chris, 2026-09-26):
  - A night with no treatments list (history, Brain-only) is classified from
    glucose alone: late_meal and treated_low are INFERRED; late_correction,
    basal_late / basal_missed, exercise, and away cannot be seen and are
    never asserted. Such a night with no visible problem is ["clean"] with
    code_source "inferred", so every card says the codes are inferred.
  - Time below range divides by EXPECTED readings (hours x 12), as the
    spec's worked example does (58 / 1,440 = 4.03%).
  - A 15-min median at time T uses the readings in (T - 15 min, T] and needs
    at least 2 of them, else None. Durations (minutes below 70, the 120-min
    ketone run, the 20-min unfelt run) count readings x 5 min.
  - "Treated low": a low followed by a rise of MORE THAN 60 mg/dL above its
    nadir within 2 h (a rebound, which is what treatment looks like)."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

import numpy as np

LOW = 70.0
LEVEL2 = 54.0
KETONE = 200.0
KETONE_MIN = 120
READING_MIN = 5
READINGS_PER_HOUR = 12
COVERAGE_OK = 0.85            # a night under it is stale (0.85 x 108 = 91.8 -> 92 readings)
WINDOW_COVERAGE_OK = 0.70     # Step Watch window under it is insufficient
MEDIAN_MIN = 15
RUN_GAP_MIN = 30              # a longer gap between readings breaks a run (step 1's gap)
NEAR_MISS_MIN = 60
LATE_MEAL_H = 3
LATE_CORRECTION_H = 3
BASAL_LATE_MIN = 60
EXERCISE_AFTER = time(17, 0)
TREATED_RISE = 60.0
TREATED_WITHIN_H = 2
INFERRED_MEAL_RATE = 2.0      # mg/dL per min over 15 min, in the first 2 h of the night
INFERRED_MEAL_H = 2
UNFELT_MIN = 20
UNFELT_SLOPE = 1.0
RECOVERY_MIN = 30
CARBS_WITHIN_MIN = 30
UNFELT_ANSWERS = {"woke_no_symptoms", "dont_remember"}
EXERCISE_WORDS = ("exercise", "workout", "run", "gym", "walk", "swim", "bike", "ride", "sport")

_EPOCH = datetime(1970, 1, 1)


# ---------------------------------------------------------------- helpers

def _get(o, name, default=None):
    if isinstance(o, dict):
        return o.get(name, default)
    return getattr(o, name, default)


def _sec(dt: datetime) -> float:
    if dt.tzinfo is None:
        return (dt - _EPOCH).total_seconds()
    return dt.timestamp()


def _series(readings) -> tuple[np.ndarray, np.ndarray]:
    """Non-stale readings as sorted (seconds, mg/dL), exact duplicates removed."""
    rows = [(_sec(_get(r, "timestamp")), float(_get(r, "glucose_mgdl", _get(r, "mgdl"))))
            for r in (readings or []) if not _get(r, "is_stale", False)]
    if not rows:
        return np.zeros(0), np.zeros(0)
    a = np.array(sorted(rows))
    keep = np.concatenate([[True], np.diff(a[:, 0]) != 0])
    return a[keep, 0], a[keep, 1]


def _window(night_window) -> tuple[datetime, datetime, float, float]:
    start, end = night_window
    return start, end, _sec(start), _sec(end)


def _median15(t: np.ndarray, x: np.ndarray, at: float) -> float | None:
    m = (t > at - MEDIAN_MIN * 60) & (t <= at)
    return float(np.median(x[m])) if m.sum() >= 2 else None


def _rolling_median15(t: np.ndarray, x: np.ndarray) -> np.ndarray:
    out = np.full(len(x), np.nan)
    lo = np.searchsorted(t, t - MEDIAN_MIN * 60, side="right")
    for i in range(len(x)):
        if i - lo[i] + 1 >= 2:
            out[i] = np.median(x[lo[i]:i + 1])
    return out


def _runs(t: np.ndarray, cond: np.ndarray) -> list[tuple[int, int]]:
    """Maximal index runs where cond holds and no gap exceeds RUN_GAP_MIN."""
    runs, i, n = [], 0, len(t)
    while i < n:
        if not cond[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and cond[j + 1] and t[j + 1] - t[j] <= RUN_GAP_MIN * 60:
            j += 1
        runs.append((i, j))
        i = j + 1
    return runs


def _episodes(t: np.ndarray, x: np.ndarray, below: float) -> list[tuple[int, int]]:
    """Excursions under `below` confirmed by 2 consecutive readings; an episode
    ends after 2 consecutive readings back at or above `below`. Returns
    (first reading, last reading under) index pairs."""
    eps, i, n = [], 0, len(x)
    while i + 1 < n:
        if x[i] < below and x[i + 1] < below and t[i + 1] - t[i] <= RUN_GAP_MIN * 60:
            j, last, above = i + 1, i + 1, 0
            while j + 1 < n and above < 2 and t[j + 1] - t[j] <= RUN_GAP_MIN * 60:
                j += 1
                if x[j] < below:
                    last, above = j, 0
                else:
                    above += 1
            eps.append((i, last))
            i = j + 1
        else:
            i += 1
    return eps


def _rebound(t: np.ndarray, x: np.ndarray, from_sec: float) -> bool:
    """A rise of more than TREATED_RISE above the running minimum within
    TREATED_WITHIN_H of from_sec: what a treated low looks like."""
    m = (t >= from_sec) & (t <= from_sec + TREATED_WITHIN_H * 3600)
    if m.sum() < 2:
        return False
    seg = x[m]
    return bool((seg - np.minimum.accumulate(seg)).max() > TREATED_RISE)


def _kind(tr) -> str:
    return str(_get(tr, "kind", ""))


def _in(tr, lo: float, hi: float) -> bool:
    s = _sec(_get(tr, "timestamp"))
    return lo <= s < hi


def _is_carbs(tr) -> bool:
    return _kind(tr) == "carbs" or (_get(tr, "carbs_g") or 0) > 0


def _is_stale(record) -> bool:
    """A night record is stale when coded so or under 85% coverage."""
    cov = _get(record, "coverage_pct")
    return "stale" in (_get(record, "reason_codes") or []) or (cov is not None and cov < 100 * COVERAGE_OK)


def _low_alarms(alarm_events, tiers=("actual_low",)):
    return [a for a in (alarm_events or []) if _get(a, "tier") in tiers]


# ---------------------------------------------------------------- 4.2 night metrics

def night_metrics(readings, alarm_events, night_window, *, day_readings=None) -> dict:
    """coverage_pct, rise_mgdl, dawn_rise_mgdl, low_point_mgdl, tbr_pct (and
    tbr_pct_day when day_readings, 24 h of them, are given), minutes_below_70,
    auc_below_70, near_miss_count, level2_count, ketone_risk_episodes, plus
    readings / expected / stale. Stale-flagged readings never count."""
    start, end, s0, s1 = _window(night_window)
    t_all, x_all = _series(readings)
    m = (t_all >= s0) & (t_all < s1)
    t, x = t_all[m], x_all[m]
    expected = (s1 - s0) / 3600 * READINGS_PER_HOUR
    n = len(x)
    coverage = min(100.0, 100.0 * n / expected) if expected > 0 else 0.0

    end_med = _median15(t_all, x_all, s1)
    start_med = _median15(t_all, x_all, s0 + 2 * 3600)
    rise = end_med - start_med if end_med is not None and start_med is not None else None
    dawn_at = datetime.combine(end.date(), time(3, 0), tzinfo=end.tzinfo)
    dawn_med = _median15(t_all, x_all, _sec(dawn_at)) if start <= dawn_at < end else None
    dawn = end_med - dawn_med if end_med is not None and dawn_med is not None else None

    roll = _rolling_median15(t, x)
    low_point = float(np.nanmin(roll)) if np.isfinite(roll).any() else None

    below = x < LOW
    out = {
        "readings": n,
        "expected": expected,
        "coverage_pct": coverage,
        "stale": coverage < 100 * COVERAGE_OK,
        "rise_mgdl": rise,
        "dawn_rise_mgdl": dawn,
        "low_point_mgdl": low_point,
        "tbr_pct": 100.0 * below.sum() / expected if expected > 0 else None,
        "tbr_pct_day": None,
        "minutes_below_70": int(below.sum() * READING_MIN),
        "auc_below_70": float(((LOW - x[below]) * READING_MIN).sum()),
        "near_miss_count": near_misses(t_all, x_all, alarm_events, s0, s1),
        "level2_count": len(_episodes(t, x, LEVEL2)),
        "ketone_risk_episodes": ketone_risk_episodes(t, x),
    }
    if day_readings is not None:
        td, xd = _series(day_readings)
        out["tbr_pct_day"] = 100.0 * (xd < LOW).sum() / (24 * READINGS_PER_HOUR)
    return out


def near_misses(t: np.ndarray, x: np.ndarray, alarm_events, s0: float, s1: float) -> int:
    """predicted_low episodes started in [s0, s1) with crossed_actual false and
    no reading under 70 within 60 min of their start."""
    count = 0
    for a in _low_alarms(alarm_events, ("predicted_low",)):
        st = _sec(_get(a, "started_at"))
        if not (s0 <= st < s1) or _get(a, "crossed_actual", False):
            continue
        after = (t >= st) & (t <= st + NEAR_MISS_MIN * 60)
        if not (x[after] < LOW).any():
            count += 1
    return count


def ketone_risk_episodes(t: np.ndarray, x: np.ndarray) -> int:
    """Runs at or above 200 mg/dL lasting at least 120 min (readings x 5)."""
    return sum((j - i + 1) * READING_MIN >= KETONE_MIN for i, j in _runs(t, x >= KETONE))


# ---------------------------------------------------------------- 4.1 classification

def classify_night(readings, treatments, alarm_events, presence, night_window, *,
                   usual_basal_time: str | None = None) -> tuple[list[str], str]:
    """-> (reason_codes, code_source). treatments=None means nothing is logged
    (history, Brain-only): glucose-only inference, code_source "inferred".
    A list (even empty) means a logging patient: code_source "logged".
    presence: None, one PresenceState, or a list of them (transitions)."""
    start, end, s0, s1 = _window(night_window)
    t_all, x_all = _series(readings)
    m = (t_all >= s0) & (t_all < s1)
    t, x = t_all[m], x_all[m]
    codes: list[str] = []

    expected = (s1 - s0) / 3600 * READINGS_PER_HOUR
    if len(x) < COVERAGE_OK * expected:
        codes.append("stale")

    if treatments is None:
        if _inferred_meal(t, x, s0):
            codes.append("late_meal")
        if any(_rebound(t_all, x_all, t[i]) for i in np.flatnonzero(x < LOW)):
            codes.append("treated_low")
        source = "inferred"
    else:
        trs = list(treatments)
        if any(_is_carbs(tr) and _in(tr, s0 - LATE_MEAL_H * 3600, s1) for tr in trs):
            codes.append("late_meal")
        if any(_kind(tr) == "bolus" and _in(tr, s0 - LATE_CORRECTION_H * 3600, s0) for tr in trs):
            codes.append("late_correction")
        codes += _basal_codes(trs, usual_basal_time, start, end)
        ex_from = _sec(datetime.combine(start.date(), EXERCISE_AFTER, tzinfo=start.tzinfo))
        if any(_is_exercise(tr) and _in(tr, ex_from, s1) for tr in trs):
            codes.append("exercise")
        if alarm_events is not None:
            treated = any(s0 <= _sec(_get(a, "started_at")) < s1 and _rebound(t_all, x_all, _sec(_get(a, "started_at")))
                          for a in _low_alarms(alarm_events))
        else:
            treated = any(_rebound(t_all, x_all, t[i]) for i in np.flatnonzero(x < LOW))
        if treated:
            codes.append("treated_low")
        source = "logged"

    if _away(presence, s0, s1):
        codes.append("away")
    return (codes or ["clean"]), source


def _inferred_meal(t: np.ndarray, x: np.ndarray, s0: float) -> bool:
    """A rise faster than INFERRED_MEAL_RATE mg/dL/min over 15 min in the
    night's first 2 h (reading 15 +/- 2.5 min earlier)."""
    for i in np.flatnonzero(t < s0 + INFERRED_MEAL_H * 3600):
        j = np.searchsorted(t, t[i] - MEDIAN_MIN * 60)
        for k in (j - 1, j):
            if 0 <= k < i and abs(t[i] - t[k] - MEDIAN_MIN * 60) <= 150:
                if (x[i] - x[k]) / ((t[i] - t[k]) / 60) > INFERRED_MEAL_RATE:
                    return True
    return False


def _basal_codes(trs, usual: str | None, start: datetime, end: datetime) -> list[str]:
    """Only with a usual basal time: the expected dose is its latest
    occurrence at or before the window end."""
    if not usual:
        return []
    hh, mm = (int(v) for v in usual.split(":"))
    expected = datetime.combine(end.date(), time(hh, mm), tzinfo=end.tzinfo)
    if expected > end:
        expected -= timedelta(days=1)
    lo, hi = _sec(expected) - 3 * 3600, _sec(end)
    doses = sorted(_sec(_get(tr, "timestamp")) for tr in trs if _kind(tr) == "basal" and _in(tr, lo, hi))
    if not doses:
        return ["basal_missed"]
    return ["basal_late"] if doses[0] > _sec(expected) + BASAL_LATE_MIN * 60 else []


def _is_exercise(tr) -> bool:
    text = str(_get(tr, "text") or "").lower()
    return _kind(tr) == "note" and any(w in text for w in EXERCISE_WORDS)


def _away(presence, s0: float, s1: float) -> bool:
    """The presence TOGGLE set to Away at any time in the window."""
    if presence is None:
        return False
    states = sorted(presence if isinstance(presence, (list, tuple)) else [presence],
                    key=lambda p: _sec(_get(p, "since")))
    for k, p in enumerate(states):
        a = _sec(_get(p, "since"))
        b = _sec(_get(states[k + 1], "since")) if k + 1 < len(states) else float("inf")
        if _get(p, "mode") == "away" and _get(p, "source") == "toggle" and a < s1 and b > s0:
            return True
    return False


# ---------------------------------------------------------------- 4.3 low events

def low_events(readings, treatments, alarm_events, night_window) -> list[dict]:
    """One LowEvent (as a dict of its contract fields) per nocturnal low: under
    70 confirmed by 2 consecutive readings, starting inside the window; a new
    event needs 2 readings back at or above 70. inferred_unfelt = at least 20
    min under 70, recovery slope under 1.0 mg/dL/min over the 30 min after the
    nadir, no carbs within 30 min, and never on a treated low."""
    start, end, s0, s1 = _window(night_window)
    t, x = _series(readings)
    trs = list(treatments or [])
    tz = start.tzinfo
    out = []
    for i, last in _episodes(t, x, LOW):
        if not (s0 <= t[i] < s1):
            continue
        seg = slice(i, last + 1)
        under = x[seg] < LOW
        k = i + int(np.argmin(x[seg]))
        nadir, t_nadir = float(x[k]), t[k]
        slope = _recovery_slope(t, x, k)
        carbs = any(_is_carbs(tr) and _in(tr, t[i], t[i] + CARBS_WITHIN_MIN * 60) for tr in trs)
        treated = carbs or _rebound(t, x, t[i])
        minutes = int(under.sum() * READING_MIN)
        unfelt = (not treated and minutes >= UNFELT_MIN and slope is not None and slope < UNFELT_SLOPE)
        started = _dt(t[i], tz)
        out.append({
            "low_event_id": f"low-{started:%Y%m%dT%H%M%S}",
            "night_date": start.date(),
            "started_at": started,
            "nadir_mgdl": nadir,
            "nadir_at": _dt(t_nadir, tz),
            "minutes_below_70": minutes,
            "auc_below_70": float(((LOW - x[seg][under]) * READING_MIN).sum()),
            "recovery_slope": slope,
            "carbs_logged_within_30min": carbs,
            "inferred_unfelt": bool(unfelt),
            "alarm_event_id": _matching_alarm(alarm_events, t[i]),
        })
    return out


def _recovery_slope(t: np.ndarray, x: np.ndarray, k: int) -> float | None:
    """mg/dL per min from the nadir to the reading nearest nadir + 30 min
    (within 5 min of it); None when there is none."""
    target = t[k] + RECOVERY_MIN * 60
    j = int(np.argmin(np.abs(t - target)))
    if abs(t[j] - target) > 5 * 60 or t[j] <= t[k]:
        return None
    return float((x[j] - x[k]) / ((t[j] - t[k]) / 60))


def _matching_alarm(alarm_events, st: float) -> str | None:
    best, gap = None, 10 * 60
    for a in _low_alarms(alarm_events, ("actual_low",)):
        d = abs(_sec(_get(a, "started_at")) - st)
        if d <= gap:
            best, gap = _get(a, "event_id"), d
    return best


def _dt(sec: float, tz) -> datetime:
    if tz is None:
        return _EPOCH + timedelta(seconds=float(sec))
    return datetime.fromtimestamp(float(sec), tz)


# ---------------------------------------------------------------- 4.4 standing window

def standing_window(night_records, low_events, recalls, *, alarm_events=(),
                    alarm_source: str = "measured") -> tuple[dict, dict]:
    """Over the nights given (the 14-night window): values + a parallel
    confidence dict. alarm_source: "measured" on the device, "inferred" when
    the alarm events were replayed from history. None, never zero, for what
    cannot be computed."""
    nights = list(night_records or [])
    clean, excluded = [], []
    for r in nights:
        codes = list(_get(r, "reason_codes") or [])
        stale = _is_stale(r)
        if codes == ["clean"] and not stale:
            clean.append(r)
        else:
            reasons = [c for c in codes if c != "clean"]
            if stale and "stale" not in reasons:
                reasons.insert(0, "stale")
            excluded.append({"night_date": _get(r, "night_date"), "reasons": reasons})

    rises = np.array([_get(r, "rise_mgdl") for r in clean if _get(r, "rise_mgdl") is not None], dtype=float)
    median_rise = float(np.median(rises)) if len(rises) else None
    if median_rise is None or median_rise == 0:
        share = None
    else:
        share = float((np.sign(rises) == np.sign(median_rise)).sum() / len(rises))

    lows = list(low_events or [])
    answers = {_get(rc, "low_event_id"): _get(rc, "answer") for rc in (recalls or [])}
    answered = [answers.get(_get(e, "low_event_id")) for e in lows if answers.get(_get(e, "low_event_id")) is not None]
    unfelt = sum(a in UNFELT_ANSWERS for a in answered)
    inferred_unanswered = sum(bool(_get(e, "inferred_unfelt")) and answers.get(_get(e, "low_event_id")) is None
                              for e in lows)

    alarms = list(alarm_events or [])
    acks = [(_sec(_get(a, "acknowledged_at")) - _sec(_get(a, "started_at"))) / 60
            for a in _low_alarms(alarms, ("predicted_low", "actual_low"))
            if _get(a, "presence_during") == "home" and _get(a, "acknowledged_at") is not None]

    values = {
        "nights": len(nights),
        "clean_nights": len(clean),
        "rise_median_clean": median_rise,
        "same_direction_share": share,
        "near_misses": int(sum(_get(r, "near_miss_count") or 0 for r in nights)),
        "escalated_warnings": sum(bool(_get(a, "escalated")) for a in _low_alarms(alarms, ("predicted_low",))),
        "rearms": int(sum(_get(a, "rearm_count") or 0 for a in _low_alarms(alarms, ("predicted_low", "actual_low")))),
        "median_ack_min": float(np.median(acks)) if acks else None,
        "nocturnal_lows": len(lows),
        "answered": len(answered),
        "unfelt_lows": unfelt,
        "no_answer": len(lows) - len(answered),
        "unfelt_low_rate": unfelt / len(answered) if answered else None,
        "inferred_unfelt_unanswered": inferred_unanswered,
        "excluded_nights": excluded,
    }
    codes_label = "measured" if clean and all(_get(r, "code_source") == "logged" for r in clean) else "inferred"
    confidence = {
        "clean_nights": codes_label,
        "rise_median_clean": codes_label,
        "same_direction_share": codes_label,
        "near_misses": alarm_source,
        "escalated_warnings": alarm_source,
        "rearms": alarm_source,
        "median_ack_min": alarm_source,
        "nocturnal_lows": "measured",
        "answered": "reported",
        "unfelt_lows": "reported",
        "no_answer": "reported",
        "unfelt_low_rate": "reported",
        "inferred_unfelt_unanswered": "inferred",
    }
    return values, confidence


# ---------------------------------------------------------------- 4.5 step window

def step_window_metrics(window_records, baseline_records, symptom_checks, injections, *,
                        window_readings=None, window_days: int | None = None, window_dates=None,
                        expected_injections: int | None = None, expected_dose_label: str | None = None,
                        alarm_source: str = "measured") -> tuple[dict, dict]:
    """A Step Watch window against its baseline: values + confidence dict.
    window_readings: every reading in the window's days (coverage and TBR are
    per expected reading, window_days x 288); window_dates: the days whose
    symptom checks count (default: the window records' night dates)."""
    def low_points(records):
        return np.array([_get(r, "low_point_mgdl") for r in records or []
                         if _get(r, "low_point_mgdl") is not None and not _is_stale(r)], dtype=float)

    win_lp, base_lp = low_points(window_records), low_points(baseline_records)
    shift = float(np.median(win_lp) - np.median(base_lp)) if len(win_lp) and len(base_lp) else None

    coverage = tbr = ketone = None
    if window_readings is not None and window_days:
        t, x = _series(window_readings)
        expected = window_days * 24 * READINGS_PER_HOUR
        coverage = min(100.0, 100.0 * len(x) / expected)
        tbr = 100.0 * (x < LOW).sum() / expected
        ketone = ketone_risk_episodes(t, x)

    dates = list(window_dates) if window_dates is not None else [_get(r, "night_date") for r in window_records or []]
    checks = {_get(c, "date"): _get(c, "gi") for c in symptom_checks or [] if _get(c, "date") in set(dates)}
    tolerance = {"fine": 0, "rough": 0, "cant_eat": 0, "missing": 0}
    for d in dates:
        tolerance[checks.get(d, "missing")] += 1

    shots = [tr for tr in injections or [] if _kind(tr) == "glp1_dose"]
    mismatch = (expected_dose_label is not None
                and any(_get(tr, "dose_label") not in (None, expected_dose_label) for tr in shots))
    adherence = {
        "logged": len(shots),
        "expected": expected_injections,
        "missed": max(0, expected_injections - len(shots)) if expected_injections is not None else None,
        "dose_mismatch": bool(mismatch),
    }

    values = {
        "low_point_shift": shift,
        "window_low_point": float(np.median(win_lp)) if len(win_lp) else None,
        "baseline_low_point": float(np.median(base_lp)) if len(base_lp) else None,
        "baseline_nights": len(base_lp),
        "coverage_pct": coverage,
        "insufficient": None if coverage is None else coverage < 100 * WINDOW_COVERAGE_OK,
        "tbr_pct": tbr,
        "near_misses": int(sum(_get(r, "near_miss_count") or 0 for r in window_records or [])),
        "ketone_risk_episodes": ketone,
        "tolerance": tolerance,
        "adherence": adherence,
    }
    confidence = {
        "low_point_shift": "measured",
        "baseline_nights": "measured",
        "coverage_pct": "measured",
        "tbr_pct": "measured",
        "near_misses": alarm_source,
        "ketone_risk_episodes": "measured",
        "tolerance": "reported",
        "adherence": "reported",
    }
    return values, confidence
