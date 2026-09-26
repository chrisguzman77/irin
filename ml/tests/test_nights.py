"""george.md step 4: nights.py against the spec's worked examples, by hand,
and the boundaries the backend's Standing Card and Step Watch tests lean on.
All data SYNTHETIC."""

from datetime import date, datetime, timedelta

import numpy as np
import pytest

from ml.models import nights as N

START = datetime(2020, 1, 1, 22, 0)
END = datetime(2020, 1, 2, 7, 0)
WIN = (START, END)


def at(h, m=0, day=1):
    return datetime(2020, 1, day, h, m)


def series(fn, start=START, minutes=9 * 60 + 60, step=5):
    """Readings every `step` min from `start`; fn(minutes since start) -> mg/dL."""
    return [{"timestamp": start + timedelta(minutes=k), "glucose_mgdl": float(fn(k))}
            for k in range(0, minutes, step)]


def flat(v=120.0):
    return series(lambda k: v)


# ------------------------------------------------------------ coverage / stale

def test_coverage_92_of_108_is_adequate_91_is_stale():
    assert 0.85 * 108 == pytest.approx(91.8)
    full = series(lambda k: 120, minutes=9 * 60)                   # exactly the 108 window slots
    assert len(full) == 108
    m92 = N.night_metrics(full[:92], None, WIN)
    m91 = N.night_metrics(full[:91], None, WIN)
    assert m92["coverage_pct"] == pytest.approx(100 * 92 / 108) and not m92["stale"]
    assert m91["stale"]
    assert "stale" not in N.classify_night(full[:92], None, None, None, WIN)[0]
    assert "stale" in N.classify_night(full[:91], None, None, None, WIN)[0]


def test_stale_flagged_readings_never_count():
    rows = flat()
    for r in rows[:30]:
        r["is_stale"] = True
    m = N.night_metrics(rows, None, WIN)
    assert m["readings"] == 108 - 30


# ------------------------------------------------------------ rise / dawn / low point

def test_rise_and_dawn_are_15_min_medians():
    # 100 all night, 142 from 06:30: rise (07:00 vs 00:00) = +42, dawn (07:00 vs 03:00) = +42
    rows = series(lambda k: 142 if k >= 8 * 60 + 30 else 100)
    m = N.night_metrics(rows, None, WIN)
    assert m["rise_mgdl"] == 42 and m["dawn_rise_mgdl"] == 42


def test_median_needs_two_readings_else_none():
    rows = [r for r in flat() if not (r["timestamp"] > END - timedelta(minutes=15) and r["timestamp"] <= END)]
    rows.append({"timestamp": END, "glucose_mgdl": 120.0})           # only one reading in (06:45, 07:00]
    assert N.night_metrics(rows, None, WIN)["rise_mgdl"] is None


def test_low_point_is_min_of_rolling_median_not_a_single_dip():
    rows = series(lambda k: 50 if k == 120 else (76 if 200 <= k < 260 else 110))
    m = N.night_metrics(rows, None, WIN)
    assert m["low_point_mgdl"] == 76                                  # the lone 50 never survives a median


# ------------------------------------------------------------ burden counts

def test_tbr_minutes_auc_and_level2():
    rows = series(lambda k: {100: 60, 105: 60, 110: 60, 300: 50, 400: 52, 405: 53}.get(k, 120))
    m = N.night_metrics(rows, None, WIN)
    below = 6
    assert m["minutes_below_70"] == below * 5
    assert m["auc_below_70"] == (10 * 3 + 20 + 18 + 17) * 5
    assert m["tbr_pct"] == pytest.approx(100 * below / 108)
    assert m["level2_count"] == 1                                    # 52, 53 confirmed; the lone 50 is not


def test_ketone_risk_needs_120_minutes():
    r24 = series(lambda k: 210 if 60 <= k < 60 + 24 * 5 else 150)
    r23 = series(lambda k: 210 if 60 <= k < 60 + 23 * 5 else 150)
    assert N.night_metrics(r24, None, WIN)["ketone_risk_episodes"] == 1
    assert N.night_metrics(r23, None, WIN)["ketone_risk_episodes"] == 0


def test_near_miss_needs_no_crossing_within_60_min():
    rows = series(lambda k: 65 if k == 3 * 60 + 30 else 110)        # a reading under 70 at 01:30
    ev = [
        {"event_id": "a", "tier": "predicted_low", "started_at": at(0, 0, 2), "crossed_actual": False},   # no crossing: near miss
        {"event_id": "b", "tier": "predicted_low", "started_at": at(1, 0, 2), "crossed_actual": False},   # 01:30 < 60 min later
        {"event_id": "c", "tier": "predicted_low", "started_at": at(4, 0, 2), "crossed_actual": True},
        {"event_id": "d", "tier": "actual_low", "started_at": at(5, 0, 2)},
    ]
    assert N.night_metrics(rows, ev, WIN)["near_miss_count"] == 1


# ------------------------------------------------------------ classify_night (logged)

def carbs(ts):
    return {"timestamp": ts, "kind": "carbs", "carbs_g": 15}


def test_snack_3h01_before_night_start_is_not_late_meal():
    codes, src = N.classify_night(flat(), [carbs(START - timedelta(hours=3, minutes=1))], [], None, WIN)
    assert codes == ["clean"] and src == "logged"
    codes, _ = N.classify_night(flat(), [carbs(START - timedelta(hours=3))], [], None, WIN)
    assert codes == ["late_meal"]


def test_late_correction_basal_and_exercise():
    bolus = {"timestamp": START - timedelta(hours=2), "kind": "bolus", "insulin_units": 2, "confirmed": True}
    assert "late_correction" in N.classify_night(flat(), [bolus], [], None, WIN)[0]

    def basal(h, m):
        return {"timestamp": at(h, m), "kind": "basal", "insulin_units": 20, "confirmed": True}
    assert N.classify_night(flat(), [basal(22, 0)], [], None, WIN, usual_basal_time="21:00")[0] == ["clean"]
    assert "basal_late" in N.classify_night(flat(), [basal(22, 1)], [], None, WIN, usual_basal_time="21:00")[0]
    assert "basal_missed" in N.classify_night(flat(), [], [], None, WIN, usual_basal_time="21:00")[0]
    assert N.classify_night(flat(), [], [], None, WIN)[0] == ["clean"]          # no usual time: never asserted

    def note(h, m):
        return {"timestamp": at(h, m), "kind": "note", "text": "evening run"}
    assert "exercise" in N.classify_night(flat(), [note(17, 0)], [], None, WIN)[0]
    assert "exercise" not in N.classify_night(flat(), [note(16, 59)], [], None, WIN)[0]


def test_away_only_from_the_toggle():
    toggle = {"mode": "away", "source": "toggle", "since": at(23, 0)}
    radar = {"mode": "away", "source": "radar", "since": at(23, 0)}
    assert "away" in N.classify_night(flat(), [], [], toggle, WIN)[0]
    assert "away" not in N.classify_night(flat(), [], [], radar, WIN)[0]
    back_home = [toggle | {"since": at(12, 0)}, {"mode": "home", "source": "toggle", "since": at(20, 0)}]
    assert "away" not in N.classify_night(flat(), [], [], back_home, WIN)[0]


def treated_curve(rebound):
    # 110, a low to 60 at 02:00, then a rise of `rebound` above the nadir by 03:00
    def f(k):
        if k < 4 * 60 - 15:
            return 110
        if k < 4 * 60 + 5:
            return 60
        return 60 + min(rebound, (k - (4 * 60 + 5)) * rebound / 55)
    return series(f)


def test_treated_low_is_a_rebound_of_more_than_60():
    alarm = [{"event_id": "x", "tier": "actual_low", "started_at": at(1, 45, 2)}]
    assert "treated_low" in N.classify_night(treated_curve(65), [], alarm, None, WIN)[0]
    assert "treated_low" not in N.classify_night(treated_curve(55), [], alarm, None, WIN)[0]


# ------------------------------------------------------------ classify_night (history, inferred)

def test_history_inferred_codes_and_clean():
    codes, src = N.classify_night(flat(), None, None, None, WIN)
    assert codes == ["clean"] and src == "inferred"
    fast = series(lambda k: 110 if k < 30 else (110 + 35 if k >= 45 else 110 + (k - 30) * 35 / 15))
    slow = series(lambda k: 110 if k < 30 else (110 + 30 if k >= 45 else 110 + (k - 30) * 30 / 15))
    assert "late_meal" in N.classify_night(fast, None, None, None, WIN)[0]     # 35 / 15 = 2.33 mg/dL/min
    assert "late_meal" not in N.classify_night(slow, None, None, None, WIN)[0]  # 30 / 15 = 2.0, not faster
    assert "treated_low" in N.classify_night(treated_curve(65), None, None, None, WIN)[0]
    assert "treated_low" not in N.classify_night(treated_curve(55), None, None, None, WIN)[0]


# ------------------------------------------------------------ low events

def unfelt_curve(slope):
    # falls to a nadir of 55 at 02:00, then rises at `slope` mg/dL/min for 30 min, then flat
    nadir_k = 4 * 60

    def f(k):
        if k < nadir_k - 20:
            return 100
        if k < nadir_k:
            return {nadir_k - 20: 68, nadir_k - 15: 62, nadir_k - 10: 58, nadir_k - 5: 56}[k]
        if k <= nadir_k + 30:
            return 55 + slope * (k - nadir_k)
        return 55 + slope * 30
    return series(f)


def test_recovery_slope_099_is_inferred_unfelt_and_100_is_not():
    e99 = N.low_events(unfelt_curve(0.99), [], [], WIN)
    e100 = N.low_events(unfelt_curve(1.0), [], [], WIN)
    assert len(e99) == len(e100) == 1
    assert e99[0]["recovery_slope"] == pytest.approx(0.99) and e99[0]["inferred_unfelt"]
    assert e100[0]["recovery_slope"] == pytest.approx(1.0) and not e100[0]["inferred_unfelt"]
    e = e99[0]
    assert e["nadir_mgdl"] == 55 and e["nadir_at"] == at(2, 0, 2)
    assert e["started_at"] == at(1, 40, 2) and e["minutes_below_70"] >= 20
    assert e["low_event_id"] == "low-20200102T014000" and e["night_date"] == date(2020, 1, 1)


def test_carbs_within_30_min_is_never_inferred_unfelt():
    e = N.low_events(unfelt_curve(0.5), [carbs(at(1, 50, 2))], [], WIN)[0]
    assert e["carbs_logged_within_30min"] and not e["inferred_unfelt"]


def test_single_reading_under_70_is_not_a_low_event_and_new_event_needs_two_above():
    one = series(lambda k: 65 if k == 120 else 100)
    assert N.low_events(one, [], [], WIN) == []
    # 65, 65 (event 1), 72, 65 (same event), 72, 72, 65, 65 (event 2)
    seq = {100: 65, 105: 65, 110: 72, 115: 65, 120: 72, 125: 72, 130: 65, 135: 65}
    assert len(N.low_events(series(lambda k: seq.get(k, 100)), [], [], WIN)) == 2


def test_low_event_links_the_actual_low_alarm():
    alarm = [{"event_id": "evt-1", "tier": "actual_low", "started_at": at(1, 42, 2)}]
    assert N.low_events(unfelt_curve(0.99), [], alarm, WIN)[0]["alarm_event_id"] == "evt-1"


# ------------------------------------------------------------ standing window (worked example)

def nights_14():
    rises = [-10, -5, 35, 40, 44, 48, 50, 55]                        # 8 clean, 6 rising, median +42
    recs = [{"night_date": date(2020, 1, 1 + k), "reason_codes": ["clean"], "code_source": "inferred",
             "coverage_pct": 98.0, "rise_mgdl": float(r), "near_miss_count": 0} for k, r in enumerate(rises)]
    other = [["late_meal"], ["stale"], ["treated_low"], ["late_meal", "treated_low"], ["clean"], ["away"]]
    for k, codes in enumerate(other):
        recs.append({"night_date": date(2020, 1, 9 + k), "reason_codes": codes, "code_source": "inferred",
                     "coverage_pct": 80.0 if codes == ["clean"] else 98.0, "rise_mgdl": 90.0, "near_miss_count": 1})
    return recs


def test_standing_window_basal_check_worked_example():
    v, c = N.standing_window(nights_14(), [], [])
    assert v["nights"] == 14 and v["clean_nights"] == 8
    assert v["rise_median_clean"] == 42.0                           # (40 + 44) / 2
    assert v["same_direction_share"] == 0.75                         # 6 / 8
    assert len(v["excluded_nights"]) == 6
    assert {"night_date": date(2020, 1, 13), "reasons": ["stale"]} in v["excluded_nights"]  # clean-coded but 80%
    assert v["near_misses"] == 6
    assert c["clean_nights"] == c["rise_median_clean"] == "inferred"


def test_standing_window_unfelt_rate_divides_by_answered():
    lows = [{"low_event_id": f"l{k}", "inferred_unfelt": k == 3} for k in range(4)]
    recalls = [{"low_event_id": "l0", "answer": "woke_no_symptoms"},
               {"low_event_id": "l1", "answer": "dont_remember"},
               {"low_event_id": "l2", "answer": "felt_and_treated"},
               {"low_event_id": "l3", "answer": None}]
    v, c = N.standing_window([], lows, recalls)
    assert (v["nocturnal_lows"], v["answered"], v["unfelt_lows"], v["no_answer"]) == (4, 3, 2, 1)
    assert v["unfelt_low_rate"] == pytest.approx(2 / 3)
    assert v["inferred_unfelt_unanswered"] == 1
    assert c["unfelt_low_rate"] == "reported"


def test_standing_window_none_never_zero():
    v, _ = N.standing_window([], [{"low_event_id": "l0"}], [])
    assert v["unfelt_low_rate"] is None and v["median_ack_min"] is None
    assert v["rise_median_clean"] is None and v["same_direction_share"] is None
    assert v["no_answer"] == 1


def test_median_ack_counts_home_events_only():
    def ev(k, mins, presence, tier="predicted_low", **kw):
        s = at(1, 0, 2) + timedelta(hours=k)
        return {"event_id": f"e{k}", "tier": tier, "started_at": s, "acknowledged_at": s + timedelta(minutes=mins),
                "presence_during": presence, **kw}
    alarms = [ev(0, 2, "home", escalated=True), ev(1, 8, "home", tier="actual_low", rearm_count=1),
              ev(2, 30, "away"), ev(3, 40, "unknown")]
    v, c = N.standing_window([], [], [], alarm_events=alarms, alarm_source="inferred")
    assert v["median_ack_min"] == 5.0
    assert v["escalated_warnings"] == 1 and v["rearms"] == 1
    assert c["median_ack_min"] == "inferred"


# ------------------------------------------------------------ step window (worked example)

def test_step_window_worked_example():
    day0 = datetime(2020, 2, 3)
    slots = [day0 + timedelta(minutes=5 * k) for k in range(5 * 288)]
    keep = [s for k, s in enumerate(slots) if k % 29 != 0][:1390]      # 1,390 of 1,440 present
    assert len(keep) == 1390
    readings = [{"timestamp": s, "glucose_mgdl": 60.0 if k < 58 else 120.0} for k, s in enumerate(keep)]
    dates = [date(2020, 2, 3 + d) for d in range(5)]
    window = [{"night_date": d, "reason_codes": ["clean"], "coverage_pct": 97.0, "low_point_mgdl": lp,
               "near_miss_count": nm} for d, lp, nm in zip(dates, [74, 76, 76, 80, 78], [1, 0, 1, 0, 0])]
    baseline = [{"night_date": date(2020, 1, 1 + k), "reason_codes": ["clean"], "coverage_pct": 97.0,
                 "low_point_mgdl": lp} for k, lp in enumerate([96, 98, 98, 99, 101, 97, 100])]
    checks = [{"date": d, "gi": g} for d, g in zip(dates, ["rough", "fine", "rough", "rough", "fine"])]
    shots = [{"timestamp": day0, "kind": "glp1_dose", "dose_label": "5 mg"}]
    v, c = N.step_window_metrics(window, baseline, checks, shots, window_readings=readings, window_days=5,
                                 expected_injections=1, expected_dose_label="5 mg")
    assert v["coverage_pct"] == pytest.approx(100 * 1390 / 1440)     # 96.5%
    assert round(v["coverage_pct"], 1) == 96.5 and not v["insufficient"]
    assert v["low_point_shift"] == 76 - 98 == -22
    assert v["tbr_pct"] == pytest.approx(100 * 58 / 1440) and round(v["tbr_pct"], 2) == 4.03
    assert v["near_misses"] == 2
    assert v["tolerance"] == {"fine": 2, "rough": 3, "cant_eat": 0, "missing": 0}
    assert v["adherence"] == {"logged": 1, "expected": 1, "missed": 0, "dose_mismatch": False}
    assert v["baseline_nights"] == 7
    assert c["tolerance"] == "reported" and c["low_point_shift"] == "measured"


def test_step_window_boundaries():
    day0 = datetime(2020, 2, 3)

    def cov(n):
        rows = [{"timestamp": day0 + timedelta(minutes=5 * k), "glucose_mgdl": 120.0} for k in range(n)]
        return N.step_window_metrics([], [], [], [], window_readings=rows, window_days=5)[0]
    assert not cov(1008)["insufficient"]                             # 0.70 x 1,440 = 1,008 passes
    assert cov(1007)["insufficient"]
    v, _ = N.step_window_metrics([], [], [], [{"kind": "glp1_dose", "dose_label": "2.5 mg", "timestamp": day0}],
                                 window_dates=[date(2020, 2, 3), date(2020, 2, 4)],
                                 expected_injections=1, expected_dose_label="5 mg")
    assert v["adherence"]["dose_mismatch"] and v["tolerance"]["missing"] == 2
    assert v["low_point_shift"] is None and v["coverage_pct"] is None   # None, never zero


# ------------------------------------------------------------ Chris's three flags (2026-09-26)

def test_low_starting_before_the_window_and_running_into_it_is_this_nights_event():
    # under 70 from 21:40 to 22:20: no other night can own it
    rows = series(lambda k: 60 if -20 <= k - 30 <= 20 else 110, start=START - timedelta(minutes=30), minutes=9 * 60 + 90)
    ev = N.low_events(rows, [], [], WIN)
    assert len(ev) == 1
    assert ev[0]["started_at"] == START - timedelta(minutes=20)       # its true start, before 22:00
    assert ev[0]["night_date"] == date(2020, 1, 1)
    # one that ENDS before the window opens is not this night's
    early = series(lambda k: 60 if -40 <= k - 60 <= -20 else 110, start=START - timedelta(minutes=60), minutes=9 * 60 + 90)
    assert N.low_events(early, [], [], WIN) == []


def test_carbs_count_from_the_low_start_to_nadir_plus_30():
    nadir = at(2, 0, 2)                                               # unfelt_curve's nadir
    at20 = N.low_events(unfelt_curve(0.5), [carbs(nadir + timedelta(minutes=20))], [], WIN)[0]
    assert at20["carbs_logged_within_30min"] and not at20["inferred_unfelt"]   # chris.md R4: treated
    at30 = N.low_events(unfelt_curve(0.5), [carbs(nadir + timedelta(minutes=30))], [], WIN)[0]
    assert at30["carbs_logged_within_30min"]
    at31 = N.low_events(unfelt_curve(0.5), [carbs(nadir + timedelta(minutes=31))], [], WIN)[0]
    assert not at31["carbs_logged_within_30min"] and at31["inferred_unfelt"]
    before = N.low_events(unfelt_curve(0.5), [carbs(at(1, 39, 2))], [], WIN)[0]   # a minute before the low began
    assert not before["carbs_logged_within_30min"]


def test_logging_patient_treated_low_without_an_alarm_record():
    # the recorder wrote nothing (restart mid-episode): the glucose rebound still marks it treated
    assert "treated_low" in N.classify_night(treated_curve(65), [], [], None, WIN)[0]
    assert "treated_low" not in N.classify_night(treated_curve(55), [], [], None, WIN)[0]
    codes, src = N.classify_night(treated_curve(65), [], [], None, WIN)
    assert src == "logged"
