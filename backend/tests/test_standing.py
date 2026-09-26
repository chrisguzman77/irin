"""R8 check at the boundaries: 5 clean nights passes, 4 does not; +30 does not
fire, +31 does; 70% same direction passes, 69% does not; near-misses at the
trigger count flip the too-high direction; Follow-up with 2 clean nights on
a side says "not enough data yet"; Hypo Response goes red on each rule; no
number is computed here (nights.py does)."""

import inspect
from datetime import date, datetime, timedelta

from app.contracts import AlarmEvent, LowEvent, LowEventRecall, NightRecord, Settings
from app.rounds import standing as standing_mod
from app.rounds.standing import Thresholds, evaluate_basal_check, evaluate_follow_up, evaluate_hypo_response

D0 = date(2020, 1, 1)


def night(i, rise=40.0, codes=("clean",), coverage=95.0, near=0, level2=0, source="logged"):
    d = D0 + timedelta(days=i)
    return NightRecord(night_date=d, window_start=datetime.combine(d, datetime.min.time()).replace(hour=22),
                       window_end=datetime.combine(d + timedelta(days=1), datetime.min.time()).replace(hour=7),
                       coverage_pct=coverage, reason_codes=list(codes), code_source=source, rise_mgdl=rise,
                       low_point_mgdl=100.0, tbr_pct=0.0, near_miss_count=near, level2_count=level2, is_demo=True)


def nights(clean_rises, excluded=("late_meal",) * 6):
    rows = [night(i, rise=r) for i, r in enumerate(clean_rises)]
    rows += [night(len(clean_rises) + i, rise=60.0, codes=(c,)) for i, c in enumerate(excluded)]
    return rows


def test_worked_example_fires_and_lists_the_excluded_nights():
    e = evaluate_basal_check(nights([42, 45, 50, 40, 42, 44, -5, -10]), [], [])  # 8 clean, 6 rising, median +42
    assert e.status == "amber" and e.flags == ["rise_high"] and e.metrics["clean_nights"] == 8
    assert e.metrics["same_direction_share"] == 0.75 and e.metrics["rise_median_clean"] == 42.0
    assert len(e.metrics["excluded_nights"]) == 6 and e.excluded_counts == {"late_meal": 6}
    assert "42" in e.headline and "6 of 8" in e.headline and e.period_start == D0
    assert all(r["code_source"] in ("logged", "inferred") for r in e.nights) and e.confidence["clean_nights"] == "measured"


def test_clean_nights_boundary_5_passes_4_does_not():
    assert evaluate_basal_check(nights([45] * 5), [], []).status == "amber"
    e = evaluate_basal_check(nights([45] * 4), [], [])
    assert e.status == "insufficient" and "5 are needed" in e.headline


def test_rise_boundary_30_does_not_fire_31_does():
    assert evaluate_basal_check(nights([30] * 6), [], []).status == "green"
    assert evaluate_basal_check(nights([31] * 6), [], []).status == "amber"
    assert evaluate_basal_check(nights([-31] * 6), [], []).flags == ["rise_low"]


def test_same_direction_boundary_70_passes_69_does_not():
    ten = [50] * 7 + [-5] * 3  # 70%
    assert evaluate_basal_check(nights(ten), [], []).status == "amber"
    thirteen = [50] * 9 + [-5] * 4  # 69.2%
    assert evaluate_basal_check(nights(thirteen), [], []).status == "green"


def test_near_misses_flip_the_too_high_direction_at_the_trigger():
    rows = nights([5] * 6)  # flat: no rise rule
    rows[0] = night(0, rise=5.0, near=Thresholds().near_miss_too_high)
    e = evaluate_basal_check(rows, [], [])
    assert e.status == "amber" and e.flags == ["rise_low"]
    rows[0] = night(0, rise=5.0, near=Thresholds().near_miss_too_high - 1)
    assert evaluate_basal_check(rows, [], []).status == "green"


def test_stale_nights_never_count():
    rows = [night(i, rise=45.0, coverage=80.0) for i in range(6)]  # under 85%: George flags stale
    e = evaluate_basal_check(rows, [], [])
    assert e.status == "insufficient" and e.metrics["clean_nights"] == 0


def test_hypo_response_rules_and_glucagon():
    rows = nights([10] * 6, excluded=())
    settings = Settings(glucagon_on_hand=True, glucagon_expiry=date(2021, 6, 1))
    green = evaluate_hypo_response(rows, [], [], [], settings)
    assert green.status == "green" and green.metrics["glucagon_on_hand"] is True and green.confidence["glucagon_on_hand"] == "reported"
    t0 = datetime(2020, 1, 2, 3, 0)
    esc = [AlarmEvent(event_id=f"e{i}", tier="predicted_low", started_at=t0 + timedelta(days=i), escalated=True,
                      presence_during="home") for i in range(2)]
    assert evaluate_hypo_response(rows, [], [], esc, settings).status == "red"
    assert evaluate_hypo_response(rows, [], [], esc[:1], settings).status == "green"
    rearm = [AlarmEvent(event_id="r", tier="actual_low", started_at=t0, rearm_count=1, acknowledged_at=t0 + timedelta(minutes=2),
                        presence_during="home")]
    assert "rearm" in evaluate_hypo_response(rows, [], [], rearm, settings).flags
    slow = [AlarmEvent(event_id="s", tier="actual_low", started_at=t0, acknowledged_at=t0 + timedelta(minutes=6), presence_during="home")]
    assert evaluate_hypo_response(rows, [], [], slow, settings).status == "red"
    away_slow = [AlarmEvent(event_id="s", tier="actual_low", started_at=t0, acknowledged_at=t0 + timedelta(minutes=6), presence_during="away")]
    assert evaluate_hypo_response(rows, [], [], away_slow, settings).status == "green"  # ack time counts on home events only
    low = LowEvent(low_event_id="l1", night_date=D0, started_at=t0, nadir_mgdl=55, nadir_at=t0, minutes_below_70=25, auc_below_70=200)
    unfelt = [LowEventRecall(low_event_id="l1", asked_at=t0, answered_at=t0, answer="woke_no_symptoms")]
    e = evaluate_hypo_response(rows, [low], unfelt, [], settings)
    assert e.status == "red" and "awareness" in e.flags and e.metrics["unfelt_low_rate"] == 1.0
    unanswered = [LowEventRecall(low_event_id="l1", asked_at=t0, answer=None)]
    e = evaluate_hypo_response(rows, [low], unanswered, [], settings)
    assert e.status == "green" and e.metrics["no_answer"] == 1 and e.metrics["unfelt_low_rate"] is None  # never counts as felt or fine


def test_follow_up_needs_3_clean_nights_per_side():
    before = nights([45] * 6, excluded=())
    after2 = [night(20 + i, rise=10.0) for i in range(2)]
    e = evaluate_follow_up(before, after2, after2)
    assert e.status == "insufficient" and "Not enough data yet" in e.headline
    after3 = [night(20 + i, rise=10.0) for i in range(3)]
    e = evaluate_follow_up(before, after3, after3)
    assert e.status == "green" and e.metrics["rise_change"] == -35.0 and "before the change" in e.headline


def test_no_number_is_computed_here():
    src = inspect.getsource(standing_mod)
    assert "numpy" not in src and "median(" not in src and "statistics" not in src
    for text in (e.headline for e in [evaluate_basal_check(nights([45] * 6), [], [])]):
        assert not any(w in text.lower() for w in ("units", "increase", "decrease", "adjust"))
