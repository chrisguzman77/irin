"""R10 check: the Step Watch rules at their boundaries (a low-point shift of
-15 fires and -14 does not; TBR 4.03 fires and 4.00 does not; rough on 3 of 5
days; coverage 70 passes and 69 is insufficient with a red still sent; two
ketone-risk episodes are highs); a hold moves every later planned start and
two holds stack; the early check is labeled limited; four green step checks
graduate; George's SYNTHETIC titration scenario through the ledger, the low
events and the service reproduces the chris.md worked example word for word
and a second run sends nothing; a watch suspends Basal Check; the endpoints
are PIN-gated; nothing is computed here (nights.py does)."""

import asyncio
import inspect
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from app import store
from app.clock import clock
from app.contracts import (AlarmEvent, DoctorMessage, LowEvent, LowEventRecall, NightRecord, Reading, Settings,
                           SymptomCheck, TitrationPlan, TitrationStep, Treatment)
from app.rounds import step_watch as mod
from app.rounds.ledger import Ledger
from app.rounds.low_events import LowEventDetector
from app.rounds.nights_adapter import NightsAdapter
from app.rounds.messages import DoctorMessages, MessageError
from app.rounds.noise import Sent, allow
from app.rounds.step_watch import (StepWatch, apply_hold, budget_key, due_kinds, evaluate_window, graduated,
                                   validate_plan)
from tests.test_ledger import Sources

SCEN = Path(__file__).resolve().parents[2] / "demo" / "scenarios"
START = date(2020, 1, 15)
H = {"X-PIN": "1234"}


def plan(started=START, n_steps=6, status="active", is_demo=True):
    steps = [TitrationStep(index=i, dose_label=f"{2.5 * (i + 1):g} mg", planned_start=started + timedelta(days=28 * i))
             for i in range(n_steps)]
    return TitrationPlan(plan_id="p1", drug_class="gip_glp1", drug_label="tirzepatide", steps=steps, started_at=started,
                         on_insulin=True, status=status, is_demo=is_demo)


def night(d, low_point=100.0, coverage=95.0, near=0, level2=0, codes=("clean",)):
    return NightRecord(night_date=d, window_start=datetime.combine(d, datetime.min.time()).replace(hour=22),
                       window_end=datetime.combine(d + timedelta(days=1), datetime.min.time()).replace(hour=7),
                       coverage_pct=coverage, reason_codes=list(codes), code_source="logged", low_point_mgdl=low_point,
                       tbr_pct=0.0, near_miss_count=near, level2_count=level2, is_demo=True)


STEP0, STEP1 = plan().steps[0], plan().steps[1]
WINDOW = [date(2020, 2, 14) + timedelta(days=i) for i in range(5)]  # step 2, days 3-7
BASE = [date(2020, 1, 1) + timedelta(days=i) for i in range(14)]


def readings(n=1440, lows=0, ketone_runs=0, t0=datetime(2020, 2, 14)):
    """n readings on the 5-minute grid from t0 (5 days x 288 = 1440): the first
    `lows` at 60, `ketone_runs` separate runs of 120 min at 220, the rest 110."""
    vals = [60.0] * lows + [110.0] * (n - lows)
    pos = lows + 10
    for _ in range(ketone_runs):
        vals[pos:pos + 24] = [220.0] * 24
        pos += 30
    return [Reading(timestamp=t0 + timedelta(minutes=5 * i), glucose_mgdl=v, trend="Flat", source="replay")
            for i, v in enumerate(vals)]


def ev(kind="step_check", window=None, baseline=None, checks=(), injections=None, recalls=(), lows=(), alarms=(),
       rdg=None, step=STEP1, dates=WINDOW, low_point=100.0):
    window = window if window is not None else [night(d, low_point=low_point) for d in dates]
    baseline = baseline if baseline is not None else [night(d) for d in BASE]
    if injections is None:
        injections = [Treatment(timestamp=datetime.combine(dates[0], datetime.min.time()), kind="glp1_dose",
                                dose_label=step.dose_label, confirmed=True)]
    return evaluate_window(plan(), kind, step, window, baseline, list(checks), list(injections), list(recalls),
                           list(lows), list(alarms), window_readings=rdg if rdg is not None else readings(),
                           window_dates=list(dates), expected_injections=1)


# ---------------------------------------------------------------- the rules at their boundaries


def test_low_point_shift_minus_15_fires_minus_14_does_not():
    e = ev(low_point=85.0)
    assert e.status == "amber" and e.flags == ["lows"] and e.metrics["low_point_shift"] == -15.0
    assert e.headline.startswith("Step 2 (5 mg), days 3 to 7. Overnight low point down 15 mg/dL from baseline, 0 near-misses, time below range 0.0%.")
    e = ev(low_point=86.0)
    assert e.status == "green" and e.flags == [] and e.kind == "step_check"


def test_tbr_4_03_fires_4_00_does_not():
    assert ev(rdg=readings(lows=58)).flags == ["lows"] and round(ev(rdg=readings(lows=58)).metrics["tbr_pct"], 2) == 4.03
    assert ev(rdg=readings(lows=57)).status == "green"
    # exactly 4.00 needs a span whose expected count divides: 25 days x 288 = 7200, 288 lows
    dates = [date(2020, 2, 14) + timedelta(days=i) for i in range(25)]
    exact = ev(dates=dates, rdg=readings(n=7200, lows=288))
    assert exact.metrics["tbr_pct"] == 4.0 and exact.status == "green"
    assert ev(dates=dates, rdg=readings(n=7200, lows=289)).flags == ["lows"]


def test_near_misses_2_fire_1_does_not():
    assert ev(window=[night(WINDOW[0], near=2)] + [night(d) for d in WINDOW[1:]]).flags == ["lows"]
    assert ev(window=[night(WINDOW[0], near=1)] + [night(d) for d in WINDOW[1:]]).status == "green"


def test_rough_on_3_of_5_days_fires_2_does_not_and_cant_eat_counts():
    checks = [SymptomCheck(date=d, gi=g, is_demo=True) for d, g in zip(WINDOW, ["rough", "rough", "fine", "rough", "fine"])]
    e = ev(checks=checks)
    assert e.status == "amber" and e.flags == ["tolerance"] and e.headline.endswith("Rough stomach 3 of 5 days.")
    assert e.metrics["tolerance"] == {"fine": 2, "rough": 3, "cant_eat": 0, "missing": 0}
    assert [d["gi"] for d in e.tolerance_days] == ["rough", "rough", "fine", "rough", "fine"]
    assert ev(checks=checks[:2]).status == "green"  # 2 rough, 3 missing: missing is never fine, but never rough either
    checks[3] = SymptomCheck(date=WINDOW[3], gi="cant_eat", is_demo=True)
    assert ev(checks=checks).flags == ["tolerance"]


def test_coverage_70_passes_69_is_insufficient_and_a_red_is_still_sent():
    e = ev(rdg=readings(n=1008))  # 1008 / 1440
    assert e.metrics["coverage_pct"] == 70.0 and e.status == "green" and e.metrics["insufficient"] is False
    e = ev(rdg=readings(n=993))  # 68.96%
    assert e.status == "insufficient" and e.flags == [] and "under 70%" in e.headline and e.metrics["insufficient"] is True
    red = ev(rdg=readings(n=993), window=[night(WINDOW[0], level2=1)] + [night(d) for d in WINDOW[1:]])
    assert red.kind == "safety" and red.status == "red" and red.flags == ["level2"]
    assert red.headline == "Step 2 (5 mg): 1 level 2 low (under 54)." and red.red_event_key == "level2:2020-02-14"


def test_red_on_a_rearm_is_keyed_to_the_episode_and_on_ketone_risk_with_cant_eat():
    alarm = AlarmEvent(event_id="ae-r", tier="actual_low", started_at=datetime(2020, 2, 16, 3, 0), rearm_count=1,
                       crossed_actual=True, is_demo=True)
    e = ev(alarms=[alarm])
    assert e.status == "red" and e.flags == ["rearm"] and e.red_event_key == "ae-r" and "1 re-armed low alarm" in e.headline
    cant = [SymptomCheck(date=WINDOW[1], gi="cant_eat", is_demo=True)]
    e = ev(rdg=readings(ketone_runs=1), checks=cant)
    assert e.status == "red" and e.flags == ["ketone_risk"] and "ketone-risk episode on a can't-eat day" in e.headline
    assert ev(rdg=readings(ketone_runs=1)).status == "green"  # one episode, nobody sick: not even amber


def test_two_ketone_episodes_are_highs():
    e = ev(rdg=readings(ketone_runs=2))
    assert e.status == "amber" and e.flags == ["highs"] and e.metrics["ketone_risk_episodes"] == 2
    assert e.headline.endswith("2 ketone-risk episodes.")


def test_awareness_from_a_recall_or_two_inferred_unfelt_unanswered():
    def low(i, inferred=True):
        t = datetime(2020, 2, 15 + i, 2, 50)
        return LowEvent(low_event_id=f"low-{i}", night_date=WINDOW[i], started_at=t, nadir_mgdl=58.0, nadir_at=t,
                        minutes_below_70=30, auc_below_70=300.0, inferred_unfelt=inferred, is_demo=True)

    recall = LowEventRecall(low_event_id="low-0", asked_at=datetime(2020, 2, 16, 7, 5), answered_at=datetime(2020, 2, 16, 7, 6),
                            answer="dont_remember", is_demo=True)
    e = ev(lows=[low(0)], recalls=[recall])
    assert e.flags == ["awareness"] and e.metrics["unfelt_lows"] == 1 and "1 low not remembered." in e.headline
    assert e.confidence["unfelt_lows"] == "reported"
    assert ev(lows=[low(0), low(1)]).flags == ["awareness"]  # two inferred, nobody asked: flagged, labeled inferred
    assert ev(lows=[low(0)]).status == "green"  # one inferred and unanswered: not yet
    felt = recall.model_copy(update={"answer": "felt_and_treated"})
    assert ev(lows=[low(0)], recalls=[felt]).status == "green"


def test_adherence_and_thin_baseline_are_flags_never_a_status():
    e = ev(injections=[])
    assert e.status == "green" and e.flags == ["missed_injection"] and e.metrics["adherence"]["missed"] == 1
    wrong = Treatment(timestamp=datetime(2020, 2, 14, 9), kind="glp1_dose", dose_label="2.5 mg", confirmed=True)
    assert ev(injections=[wrong]).flags == ["dose_mismatch"]
    e = ev(baseline=[night(d) for d in BASE[:4]])
    assert e.flags == ["baseline_thin"] and e.metrics["baseline_thin"] is True and e.status == "green"
    e = ev(baseline=[night(d, coverage=80.0) for d in BASE])  # 14 nights, all stale: none counts (invariant 9)
    assert e.flags == ["baseline_thin"] and e.metrics["baseline_nights"] == 0 and e.metrics["low_point_shift"] is None
    assert "no baseline low point to compare" in e.headline and e.status == "green"


def test_no_dose_recommendation_and_every_row_labeled():
    import re

    e = ev(low_point=80.0)
    # the only drug amount on the card is the step's own label; every other number is glucose
    assert re.findall(r"\d+(?:\.\d+)? mg(?!/dL)", e.headline) == ["5 mg"] and "unit" not in e.headline
    for k, v in e.metrics.items():
        if k not in ("window_low_point", "baseline_low_point", "baseline_thin", "insufficient", "level2_lows", "nights"):
            assert v is None or k in e.confidence, k


# ---------------------------------------------------------------- the calendar, holds, graduation


def test_due_kinds_and_the_early_check_is_labeled_limited():
    p = plan()
    assert due_kinds(p, date(2020, 1, 16)) == []
    assert due_kinds(p, date(2020, 1, 17)) == [("early_check", STEP0, (date(2020, 1, 15), date(2020, 1, 17)))]
    assert due_kinds(p, date(2020, 1, 21)) == [("step_check", STEP0, (date(2020, 1, 17), date(2020, 1, 21)))]
    assert due_kinds(p, date(2020, 2, 9)) == [("step_gate", STEP0, (date(2020, 2, 3), date(2020, 2, 9)))]
    assert due_kinds(p, date(2020, 2, 18)) == [("step_check", STEP1, (date(2020, 2, 14), date(2020, 2, 18)))]
    assert due_kinds(p, date(2020, 2, 14)) == []  # day 3 of step 2: no early check after the first step
    assert due_kinds(p.model_copy(update={"status": "ended"}), date(2020, 2, 18)) == []
    assert due_kinds(p, date(2020, 1, 10)) == []  # before the start
    early = [date(2020, 1, 15) + timedelta(days=i) for i in range(3)]
    e = ev(kind="early_check", step=STEP0, dates=early, rdg=readings(n=864, t0=datetime(2020, 1, 15)))
    assert e.limited is True and e.kind == "early_check" and e.headline.startswith("Step 1 (2.5 mg), days 1 to 3, limited data.")
    assert e.period_start == early[0] and e.period_end == early[-1]


def test_a_hold_moves_every_later_start_and_two_holds_stack():
    p = apply_hold(plan(), 4, from_index=1)
    assert [s.planned_start for s in p.steps[:2]] == [date(2020, 1, 15), date(2020, 2, 12)]
    assert p.steps[2].planned_start == date(2020, 3, 11) + timedelta(days=28)
    assert all(s.planned_start == o.planned_start + timedelta(days=28) for s, o in zip(p.steps[2:], plan().steps[2:]))
    p = apply_hold(p, 2, from_index=1)
    assert p.steps[2].planned_start == date(2020, 3, 11) + timedelta(days=42) and p.steps[5].planned_start == date(2020, 6, 3) + timedelta(days=42)
    with pytest.raises(ValueError):
        apply_hold(plan(), 3)
    assert graduated(4) and not graduated(3)


# ---------------------------------------------------------------- the service


class FakeSender:
    def __init__(self):
        self.sent = []

    async def send(self, card, event_key=None):
        await asyncio.sleep(0)  # a relay round trip: another evaluation could run here without the lock
        store.upsert_card(card, status="sent", recipients=["doc-1"], event_key=event_key)
        self.sent.append((card, event_key))
        return {"card_id": card.card_id, "kind": card.kind, "program": card.program, "status": "sent",
                "recipients": ["doc-1"], "is_demo": card.is_demo}


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(store.config, "IRIN_DB", str(tmp_path / "t.db"))
    store.init_db()
    yield
    clock.reset()


def adapter(src, brain_only=False):
    return NightsAdapter(settings=Settings(basal_time="21:30"), readings_for=src.readings_for,
                         treatments_for=src.treatments_for, alarm_events_for=src.alarm_events_for,
                         presence_for=src.presence_for, brain_only=lambda: brain_only)


def watch(src, sender=None, ad=None):
    return StepWatch(adapter=ad or adapter(src), sender=sender or FakeSender(), device_id="irin-test", is_demo=lambda: True)


def run(sw, today):
    return asyncio.run(sw.run(today=today))


def test_plan_messages_create_hold_proceed_and_end(db):
    sw = watch(Sources())
    states = []
    sw.on_plan_state = states.append
    clock.set(speed=60.0, start=datetime(2020, 2, 18, 7, 5))

    def msg(kind, **kw):
        return DoctorMessage(message_id="m", kind=kind, created_at=clock.now(), **kw)

    sw.on_plan_message(msg("plan_create", plan=plan(status="pending_confirm", is_demo=False)))
    p = sw.active_plan()
    assert p is not None and p.status == "active" and p.is_demo is True  # the world is the device's, never the sender's
    assert states[-1]["step_index"] == 1 and states[-1]["day_in_step"] == 7 and states[-1]["next_step_on"] == "2020-03-11"
    assert states[-1]["dose_label"] == "5 mg" and states[-1]["green_weeks"] == 0
    sw.on_plan_message(msg("hold_step", hold_weeks=4))
    p = sw.active_plan()
    assert p.steps[1].planned_start == date(2020, 2, 12) and p.steps[2].planned_start == date(2020, 4, 8)
    assert states[-1]["next_step_on"] == "2020-04-08"
    sw.on_plan_message(msg("proceed"))
    assert sw.active_plan() == p
    sw.on_plan_message(msg("end_watch"))
    assert sw.active_plan() is None and states[-1] == {"active": False} and sw.checkin_due() is False
    assert store.select_plans()[0].status == "ended"
    sw.on_plan_message(msg("hold_step", hold_weeks=2))  # no watch: nothing to apply, nothing raised
    assert store.select_plans()[0].status == "ended"


def test_checkin_is_one_tap_and_a_missing_day_is_never_fine(db):
    sw = watch(Sources())
    clock.set(speed=60.0, start=datetime(2020, 2, 18, 8, 0))
    store.upsert_plan(plan())
    assert sw.checkin_status() == {"symptom_check_due": True, "symptom_check_today": None}
    assert sw.checkin("rough") == SymptomCheck(date=date(2020, 2, 18), gi="rough", is_demo=True)
    assert sw.checkin_status() == {"symptom_check_due": False, "symptom_check_today": "rough"}
    sw.checkin("fine")  # the second tap of the day replaces the first
    assert store.select_symptom_checks(date(2020, 2, 18), date(2020, 2, 18), True) == [SymptomCheck(date=date(2020, 2, 18), gi="fine", is_demo=True)]
    store.upsert_symptom_check(SymptomCheck(date=date(2020, 2, 18), gi="cant_eat", is_demo=False))  # the other world, same date
    assert sw.checkin_status() == {"symptom_check_due": False, "symptom_check_today": "fine"}
    assert store.select_symptom_check(date(2020, 2, 18), False).gi == "cant_eat"
    e = ev(checks=[SymptomCheck(date=WINDOW[4], gi="rough", is_demo=True)])
    assert e.metrics["tolerance"]["missing"] == 4 and [d["gi"] for d in e.tolerance_days] == ["missing"] * 4 + ["rough"]


def test_four_green_maintenance_weeks_graduate_with_a_final_card(db):
    """A two-step plan: 2.5 mg from 01-15, the maintenance 5 mg from 02-12. Weekly
    checks on the last step (days 7, 14, 21, 28); the count survives a restart and
    a re-run of the same morning; graduation on day 28 sends the final card once."""
    store.upsert_plan(plan(n_steps=2))
    for d in BASE:
        store.upsert_night_record(night(d))
    days = [date(2020, 2, 12) + timedelta(days=i) for i in range(28)]
    for d in days:
        store.upsert_night_record(night(d))
    src = Sources(readings=readings(n=288 * 30, t0=datetime(2020, 2, 12)),
                  treatments=[Treatment(timestamp=datetime(2020, 2, 12, 9) + timedelta(days=7 * i), kind="glp1_dose",
                                        dose_label="5 mg", confirmed=True) for i in range(5)])
    sender = FakeSender()
    sw = watch(src, sender)
    states = []
    sw.on_plan_state = states.append
    seen = []
    for d in days[:14]:
        clock.set(speed=60.0, start=datetime.combine(d + timedelta(days=1), datetime.min.time()).replace(hour=7, minute=5))
        seen += [(d, e["kind"], e["status"], e["budget"]) for e in run(sw, d)]
    assert seen == [(date(2020, 2, 18), "step_check", "green", "digest"), (date(2020, 2, 25), "step_check", "green", "digest")]
    assert sw.green_weeks(sw.active_plan()) == 2 and store.get_kv("step_watch:green:p1") == '["p1:1", "p1:1:w2"]'
    assert run(sw, date(2020, 2, 25))[0]["budget"] == "digest" and sw.green_weeks(sw.active_plan()) == 2  # a re-run counts nothing
    sw = watch(src, sender)  # a restart: the count is in the store, not in memory
    sw.on_plan_state = states.append
    assert sw.green_weeks(sw.active_plan()) == 2 and sw.plan_state()["green_weeks"] == 2
    clock.set(speed=60.0, start=datetime(2020, 3, 4, 7, 5))
    assert [(e["kind"], e["budget"]) for e in run(sw, date(2020, 3, 3))] == [("step_check", "digest")]
    assert store.select_plans()[0].status == "active" and sender.sent == []  # day 21: three weeks, not four
    clock.set(speed=60.0, start=datetime(2020, 3, 11, 7, 5))
    out = run(sw, date(2020, 3, 10))
    assert [(e["kind"], e["status"], e["budget"]) for e in out] == [("step_check", "green", "digest"), ("graduation", "green", "sent")]
    assert store.select_plans()[0].status == "graduated" and sw.active_plan() is None and states[-1] == {"active": False}
    [(card, key)] = sender.sent
    assert key == "p1:graduation" and card.kind == "graduation" and (card.period_start, card.period_end) == (date(2020, 2, 12), date(2020, 3, 10))
    assert card.headline.startswith("Step 2 (5 mg), four green weeks at the maintenance dose, watch complete. Overnight low point")
    assert card.metrics["baseline_nights"] == 14 and card.allowed_actions == ["message", "dismiss"]
    assert run(sw, date(2020, 3, 10)) == []  # graduated: nothing more
    assert allow("graduation", "step_watch", "green", clock.now(), [Sent("graduation", "step_watch", "green", clock.now(), "p1:graduation")],
                 active_watch=True, event_key="p1:graduation").reason == "per_plan"


def test_an_amber_check_breaks_the_green_run_and_earlier_steps_never_count(db):
    store.upsert_plan(plan(n_steps=2))
    for d in BASE:
        store.upsert_night_record(night(d))
    sw = watch(Sources(readings=readings(n=288 * 12, t0=datetime(2020, 1, 17))))
    for d in [date(2020, 1, 17) + timedelta(days=i) for i in range(5)]:
        store.upsert_night_record(night(d))
    clock.set(speed=60.0, start=datetime(2020, 1, 22, 7, 5))
    assert run(sw, date(2020, 1, 21))[0]["status"] == "green"
    assert store.get_kv("step_watch:green:p1") == '["p1:0"]' and sw.green_weeks(sw.active_plan()) == 0  # step 1 is not maintenance
    for d in WINDOW:
        store.upsert_night_record(night(d, low_point=80.0))  # -20: amber
    sw.adapter.readings_for = Sources(readings=readings()).readings_for
    clock.set(speed=60.0, start=datetime(2020, 2, 19, 7, 5))
    assert run(sw, date(2020, 2, 18))[0]["status"] == "amber"
    assert store.get_kv("step_watch:green:p1") == "[]"


def test_a_red_on_a_non_due_day_is_sent_at_once_and_deduplicated_per_event(db):
    store.upsert_plan(plan())
    for d in BASE:
        store.upsert_night_record(night(d))
    src = Sources(readings=readings(n=288 * 12, t0=datetime(2020, 2, 19)))
    sender = FakeSender()
    sw = watch(src, sender)
    day10 = date(2020, 2, 21)  # day 10 of step 2: nothing is due
    assert due_kinds(sw.active_plan(), day10) == []
    store.upsert_night_record(night(day10, level2=1))
    clock.set(speed=60.0, start=datetime(2020, 2, 22, 7, 5))
    [e] = run(sw, day10)
    assert e["kind"] == "safety" and e["status"] == "red" and e["budget"] == "sent" and e["headline"] == "Step 2 (5 mg): 1 level 2 low (under 54)."
    assert sender.sent[-1][1] == "level2:2020-02-21" and sender.sent[-1][0].kind == "safety"
    assert run(sw, day10)[0]["budget"] == "red_duplicate" and len(sender.sent) == 1
    # a second, distinct level-2 low the next night is its own event (the 12 h cap has passed)
    store.upsert_night_record(night(day10 + timedelta(days=1), level2=1))
    clock.set(speed=60.0, start=datetime(2020, 2, 23, 7, 5))
    assert run(sw, day10 + timedelta(days=1))[0]["budget"] == "sent" and len(sender.sent) == 2
    # a re-armed low alarm at 03:00 is a red NOW (main's alarm-event hook calls safety()); the 07:05
    # run of the same night, which also sees a level 2, shares the event and is not sent again
    night_date = date(2020, 2, 23)
    alarm = AlarmEvent(event_id="ae-r", tier="actual_low", started_at=datetime(2020, 2, 24, 2, 40), rearm_count=1,
                       crossed_actual=True, is_demo=True)
    store.upsert_alarm_event(alarm)
    clock.set(speed=60.0, start=datetime(2020, 2, 24, 3, 10))
    [e] = asyncio.run(sw.safety(night_date))
    assert e["budget"] == "sent" and sender.sent[-1][1] == "ae-r" and "1 re-armed low alarm" in e["headline"]
    store.upsert_night_record(night(night_date, level2=1))
    clock.set(speed=60.0, start=datetime(2020, 2, 24, 7, 5))
    [e] = run(sw, night_date)
    assert e["budget"] == "red_duplicate" and len(sender.sent) == 3
    # a live-world red never reaches the demo watch
    store.upsert_night_record(night(date(2020, 2, 24), level2=1).model_copy(update={"is_demo": False}))
    clock.set(speed=60.0, start=datetime(2020, 2, 25, 7, 5))
    assert run(sw, date(2020, 2, 24)) == []


def test_a_hold_applies_from_the_doctors_decision_date_and_the_gate_returns(db):
    sw = watch(Sources())
    store.upsert_plan(plan())
    clock.set(speed=60.0, start=datetime(2020, 2, 13, 9, 0))  # the patient confirms on day 2 of step 2
    sw.on_plan_message(DoctorMessage(message_id="m", kind="hold_step", hold_weeks=4, created_at=datetime(2020, 2, 9, 18, 0)))
    p = sw.active_plan()
    assert p.steps[1].planned_start == date(2020, 3, 11) and p.steps[2].planned_start == date(2020, 4, 8)  # the held step moved
    assert p.steps[0].planned_start == date(2020, 1, 15)
    # the gate before the postponed step-up is a new gate: its key names the new date
    assert due_kinds(p, date(2020, 3, 8)) == [("step_gate", p.steps[0], (date(2020, 3, 2), date(2020, 3, 8)))]
    old_key, new_key = budget_key(plan(), "step_gate", STEP0, date(2020, 2, 9)), budget_key(p, "step_gate", p.steps[0], date(2020, 3, 8))
    assert (old_key, new_key) == ("p1:0:gate:2020-02-12", "p1:0:gate:2020-03-11")
    history = [Sent("step_gate", "step_watch", "amber", datetime(2020, 2, 10, 7, 5), old_key)]
    assert allow("step_gate", "step_watch", "amber", datetime(2020, 3, 9, 7, 5), history, active_watch=True, event_key=new_key).allowed
    assert not allow("step_gate", "step_watch", "amber", datetime(2020, 3, 9, 7, 5), history, active_watch=True, event_key=old_key).allowed
    assert budget_key(p, "early_check", p.steps[0], date(2020, 1, 17)) == "p1:0:early"
    assert not allow("early_check", "step_watch", "amber", datetime(2020, 1, 18), [Sent("early_check", "step_watch", "amber", datetime(2020, 1, 18), "p1:0:early")],
                     active_watch=True, event_key="p1:0:early").allowed


def test_a_new_plan_replaces_the_active_one_and_a_broken_plan_is_refused(db):
    sw = watch(Sources())
    clock.set(speed=60.0, start=datetime(2020, 2, 18, 7, 5))
    store.upsert_plan(plan())
    store.set_kv("step_watch:green:p1", '["p1:0"]')
    p2 = plan().model_copy(update={"plan_id": "p2"})
    sw.on_plan_message(DoctorMessage(message_id="m", kind="plan_create", plan=p2, created_at=clock.now()))
    assert sw.active_plan().plan_id == "p2" and {p.plan_id: p.status for p in store.select_plans()} == {"p1": "ended", "p2": "active"}
    bad = [plan().model_copy(update={"started_at": date(2020, 1, 14)}),
           plan().model_copy(update={"steps": [plan().steps[0], plan().steps[2]]}),
           plan().model_copy(update={"steps": list(reversed(plan().steps))}),
           plan().model_copy(update={"steps": []})]
    for b in bad:
        with pytest.raises(ValueError):
            validate_plan(b)
        with pytest.raises(MessageError):
            DoctorMessages._validate_kind(DoctorMessage(message_id="m", kind="plan_create", plan=b, created_at=clock.now()))
    validate_plan(p2)


def test_tolerance_counts_the_last_5_days_of_a_gate_window():
    gate = [date(2020, 2, 3) + timedelta(days=i) for i in range(7)]
    rdg = readings(n=288 * 7, t0=datetime(2020, 2, 3))
    early = [SymptomCheck(date=d, gi="rough", is_demo=True) for d in gate[:3]]  # days 1-3 of the window, none in the last 5
    e = ev(kind="step_gate", step=STEP0, dates=gate, rdg=rdg, checks=early)
    assert e.status == "green" and e.kind == "step_gate" and e.headline.endswith("Rough stomach 3 of 7 days.")
    late = [SymptomCheck(date=d, gi="rough", is_demo=True) for d in gate[2:5]]  # 3 of the last 5
    assert ev(kind="step_gate", step=STEP0, dates=gate, rdg=rdg, checks=late).flags == ["tolerance"]


def test_follow_up_is_shared_with_the_watch_and_a_weekly_basal_counts_no_shots(db):
    assert allow("follow_up", "standing", "amber", datetime(2020, 2, 18), [], active_watch=True).allowed
    assert allow("basal_check", "standing", "amber", datetime(2020, 2, 18), [], active_watch=True).reason == "watch"
    sw = watch(Sources(readings=readings()))
    p = plan().model_copy(update={"drug_class": "weekly_basal"})
    assert sw._inputs(p, (WINDOW[0], WINDOW[-1]))["expected_injections"] is None
    assert sw._inputs(plan(), (WINDOW[0], WINDOW[-1]))["expected_injections"] == 1


def test_the_synthetic_titration_reproduces_the_worked_example(db):
    """George's SYNTHETIC scenario through the replay loader, the ledger, the low
    events, the recall, the plan and the service: the step 2 check on the
    morning of 2020-02-19 says exactly what chris.md R10 says."""
    from app.datasource.replay import ReplayDataSource

    rows = ReplayDataSource(SCEN / "titration_synthetic.csv").rows
    comp = json.loads((SCEN / "titration_synthetic.json").read_text())
    rdg = [Reading(timestamp=t, glucose_mgdl=g, trend=tr, source="replay") for t, g, tr in rows]
    shots = [Treatment.model_validate(t) for t in comp["injections"]]
    alarms = [AlarmEvent.model_validate(a) for a in comp["alarm_events"]]
    for a in alarms:
        store.upsert_alarm_event(a)
    for c in comp["symptom_checks"]:
        store.upsert_symptom_check(SymptomCheck.model_validate(c))
    src = Sources(readings=rdg, treatments=shots, alarm_events=alarms)
    # the full adapter: Brain-only would drop the alarm events and with them the near-misses. A logging
    # patient with no basal logged reads basal_missed on every night here; the replay catch-up (R12)
    # overlays the companion's own reason codes, and the watch's numbers never depend on the codes.
    ad = adapter(src)
    ledger, lows = Ledger(adapter=ad, is_demo=lambda: True), LowEventDetector(adapter=ad, is_demo=lambda: True)
    for n in range(49):
        d = date(2020, 1, 1) + timedelta(days=n)
        ledger.build_night(d)
        lows.detect(d)
    for low_id, answer in comp["recall_answers"].items():
        store.upsert_recall(LowEventRecall(low_event_id=low_id, asked_at=datetime(2020, 2, 16, 7, 5),
                                           answered_at=datetime(2020, 2, 16, 7, 6), answer=answer, is_demo=True))
    assert [e.low_event_id for e in store.select_low_events(date(2020, 2, 14), date(2020, 2, 18))] == list(comp["recall_answers"])
    store.upsert_plan(TitrationPlan.model_validate(comp["plan"]))
    sender = FakeSender()
    sw = StepWatch(adapter=ad, sender=sender, device_id="irin-test", is_demo=lambda: True)
    clock.set(speed=60.0, start=datetime(2020, 2, 19, 7, 5))

    [entry] = run(sw, date(2020, 2, 18))
    assert entry["kind"] == "step_check" and entry["status"] == "amber" and entry["budget"] == "sent" and entry["step_index"] == 1
    assert entry["headline"] == ("Step 2 (5 mg), days 3 to 7. Overnight low point down 22 mg/dL from baseline, "
                                 "2 near-misses, time below range 4.0%. 1 low not remembered. Rough stomach 3 of 5 days.")
    [(card, key)] = sender.sent
    assert key == "demo-tirzepatide:1" and card.is_demo and card.plan_id == "demo-tirzepatide" and card.step_index == 1
    assert card.program == "step_watch" and card.kind == "step_check" and card.status == "amber"
    assert (card.period_start, card.period_end) == (date(2020, 2, 14), date(2020, 2, 18))
    assert card.card_id == "step_watch:step_check:demo-tirzepatide:1:2020-02-14:2020-02-18"
    m = card.metrics
    assert (m["low_point_shift"], m["baseline_low_point"], m["window_low_point"], m["baseline_nights"]) == (-22, 98, 76, 14)
    assert round(m["coverage_pct"], 1) == 96.5 and round(m["tbr_pct"], 2) == 4.03 and m["near_misses"] == 2
    assert m["tolerance"] == {"fine": 2, "rough": 3, "cant_eat": 0, "missing": 0} and m["ketone_risk_episodes"] == 0
    assert m["adherence"] == {"logged": 1, "expected": 1, "missed": 0, "dose_mismatch": False}
    assert (m["nocturnal_lows"], m["answered"], m["unfelt_lows"], m["no_answer"]) == (1, 1, 1, 0)
    assert m["insufficient"] is False and m["baseline_thin"] is False and m["level2_lows"] == 0 and m["rearms"] == 0
    assert card.confidence["near_misses"] == "measured" and card.confidence["tolerance"] == "reported"
    assert card.confidence["unfelt_lows"] == "reported" and card.confidence["low_point_shift"] == "measured"
    assert card.flags == ["lows", "awareness", "tolerance"] and card.resource_categories == ["gi_side_effect_education"]
    assert [d["gi"] for d in card.tolerance_days] == [c["gi"] for c in comp["symptom_checks"] if "2020-02-14" <= c["date"] <= "2020-02-18"]
    assert len(card.nights) == 5 and all(n["code_source"] == "logged" for n in card.nights)
    assert all(v is not None for v in m.values()) and card.source == "irin_bedside"
    assert "proceed" in card.allowed_actions and "hold_step" in card.allowed_actions
    # the same morning again (a seek that replays it): one step check per step
    assert run(sw, date(2020, 2, 18))[0]["budget"] == "per_step" and len(sender.sent) == 1
    # step 1's check was green and went to the digest; the early check was limited data
    g = run(sw, date(2020, 1, 21))
    assert g[0]["kind"] == "step_check" and g[0]["status"] == "green" and g[0]["budget"] == "digest"
    early = sw.evaluate(sw.active_plan(), "early_check", sw.active_plan().steps[0], (date(2020, 1, 15), date(2020, 1, 17)))
    assert early.limited and early.status == "green" and early.metrics["adherence"] == {"logged": 1, "expected": 1, "missed": 0, "dose_mismatch": False}


def test_a_watch_suspends_basal_check_and_the_endpoints_are_gated(monkeypatch):
    from fastapi.testclient import TestClient

    from app import auth, main
    from tests.test_evaluate import seed_nights

    monkeypatch.setattr(auth.config, "PIN", "1234")
    with TestClient(main.app) as c:
        assert c.get("/api/rounds/plan").status_code == 401 and c.get("/api/rounds/checkin").status_code == 401
        assert c.get("/api/rounds/plan", headers=H).json() == {"active": False, "plan": None}
        assert c.post("/api/rounds/checkin", json={"gi": "rough"}, headers=H).status_code == 409
        today = main.runtime.ledger.night_ended_on(main.clock.now().date())
        seed_nights(today, [42, 45, 50, 40, 42, 44, -5, -10, None, None, None, None, None, None])
        assert main.runtime.standing.active_watch() is False
        store.upsert_plan(plan(started=today - timedelta(days=10)))
        assert main.runtime.standing.active_watch() is True
        r = c.post("/api/rounds/evaluate", json={"today": today.isoformat()}, headers=H)
        budgets = {e["kind"]: e["budget"] for e in r.json()}
        assert r.status_code == 200 and budgets["basal_check"] == "watch" and budgets["hypo_response"] == "watch"
        assert c.get("/api/rounds/cards").json() == []
        assert c.get("/api/rounds/plan", headers=H).json()["plan"]["plan_id"] == "p1"
        assert c.get("/api/rounds/checkin", headers=H).json()["symptom_check_due"] is True
        assert c.post("/api/rounds/checkin", json={"gi": "rough"}).status_code == 401
        r = c.post("/api/rounds/checkin", json={"gi": "rough"}, headers=H)
        assert r.status_code == 200 and r.json()["symptom_check_today"] == "rough" and r.json()["symptom_check_due"] is False
        assert c.post("/api/rounds/checkin", json={"gi": "great"}, headers=H).status_code == 422
        assert c.post("/api/rounds/step_watch/evaluate", json={}).status_code == 401
        assert c.post("/api/rounds/step_watch/evaluate", json={"today": today.isoformat()}, headers=H).status_code == 200
        snap = c.get("/api/scheduler").json()  # the app is up with the watch wired
        assert "jobs" in snap
        main.runtime.standing.last.clear()


def test_nothing_is_computed_here():
    src = inspect.getsource(mod)
    assert "numpy" not in src and "median" not in src and "< 70" not in src and "percentile" not in src


def test_a_red_already_reported_never_swallows_the_gate_or_the_graduation(db):
    """A level-2 low on night 1 of a step goes out as a safety card that morning. The
    gate whose window holds that night is still produced (amber, flagged, with
    proceed/hold), and four green maintenance weeks still graduate with a final
    card that is not a second copy of the red."""
    store.upsert_plan(plan(n_steps=2))
    for d in BASE:
        store.upsert_night_record(night(d))
    days = [date(2020, 2, 3) + timedelta(days=i) for i in range(36)]  # 02-03 .. 03-09
    for d in days:
        store.upsert_night_record(night(d))
    sender = FakeSender()
    sw = watch(Sources(readings=readings(n=288 * 40, t0=datetime(2020, 2, 3))), sender)
    store.upsert_night_record(night(date(2020, 2, 3), level2=1))  # night 1 of the gate window
    clock.set(speed=60.0, start=datetime(2020, 2, 4, 7, 5))
    assert [e["kind"] for e in run(sw, date(2020, 2, 3))] == ["safety"] and sender.sent[-1][1] == "level2:2020-02-03"
    clock.set(speed=60.0, start=datetime(2020, 2, 10, 7, 5))
    [gate] = run(sw, date(2020, 2, 9))  # the gate before 02-12: its window is 02-03..02-09
    assert gate["kind"] == "step_gate" and gate["status"] == "amber" and gate["flags"][0] == "level2" and gate["budget"] == "sent"
    assert gate["headline"].endswith("Safety card already sent for this window.") and sender.sent[-1][0].kind == "step_gate"
    assert "proceed" in sender.sent[-1][0].allowed_actions and sender.sent[-1][1] == "p1:0:gate:2020-02-12"
    # the maintenance step: a level-2 on ITS night 1, then four green weeks
    store.upsert_night_record(night(date(2020, 2, 12), level2=1))
    clock.set(speed=60.0, start=datetime(2020, 2, 13, 7, 5))
    assert [e["kind"] for e in run(sw, date(2020, 2, 12))] == ["safety"]
    n_sent = len(sender.sent)
    for d in [date(2020, 2, 18), date(2020, 2, 25), date(2020, 3, 3)]:
        clock.set(speed=60.0, start=datetime.combine(d + timedelta(days=1), datetime.min.time()).replace(hour=7, minute=5))
        assert [(e["kind"], e["status"]) for e in run(sw, d)] == [("step_check", "green")]
    clock.set(speed=60.0, start=datetime(2020, 3, 11, 7, 5))
    out = run(sw, date(2020, 3, 10))
    assert [(e["kind"], e["status"], e["budget"]) for e in out] == [("step_check", "green", "digest"), ("graduation", "amber", "sent")]
    assert store.select_plans()[0].status == "graduated" and len(sender.sent) == n_sent + 1
    final = sender.sent[-1][0]
    assert final.kind == "graduation" and final.flags[0] == "level2" and "level 2" not in final.headline.split(".")[0]


def test_a_new_red_inside_the_graduation_window_is_a_red_not_a_graduation(db):
    store.upsert_plan(plan(n_steps=2))
    for d in BASE + [date(2020, 2, 12) + timedelta(days=i) for i in range(28)]:
        store.upsert_night_record(night(d))
    sender = FakeSender()
    sw = watch(Sources(readings=readings(n=288 * 30, t0=datetime(2020, 2, 12))), sender)
    for d in [date(2020, 2, 18), date(2020, 2, 25), date(2020, 3, 3)]:
        clock.set(speed=60.0, start=datetime.combine(d + timedelta(days=1), datetime.min.time()).replace(hour=7, minute=5))
        run(sw, d)
    store.upsert_night_record(night(date(2020, 2, 21), level2=1))  # a rebuilt night, red, nobody told
    clock.set(speed=60.0, start=datetime(2020, 3, 11, 7, 5))
    out = run(sw, date(2020, 3, 10))
    assert [(e["kind"], e["status"], e["budget"]) for e in out] == [("step_check", "green", "digest"), ("safety", "red", "sent")]
    assert sender.sent[-1][1] == "level2:2020-02-21" and store.select_plans()[0].status == "active"
    assert store.get_kv("step_watch:green:p1") == "[]"


def test_two_episodes_closing_together_send_one_red(db):
    store.upsert_plan(plan())
    sender = FakeSender()
    sw = watch(Sources(readings=readings(n=288 * 12, t0=datetime(2020, 2, 19))), sender)
    night_date = date(2020, 2, 21)
    for i in range(2):
        store.upsert_alarm_event(AlarmEvent(event_id=f"ae-{i}", tier="actual_low", started_at=datetime(2020, 2, 22, 1 + i, 0),
                                            rearm_count=1, crossed_actual=True, is_demo=True))
    clock.set(speed=60.0, start=datetime(2020, 2, 22, 2, 30))

    async def both():
        return await asyncio.gather(sw.safety(night_date), sw.safety(night_date))

    a, b = asyncio.run(both())
    assert [e["budget"] for e in a + b] == ["sent", "red_duplicate"] and len(sender.sent) == 1


def test_a_hold_stamped_by_the_doctors_wall_clock_still_holds_the_running_step(db):
    sw = watch(Sources())
    store.upsert_plan(plan())
    clock.set(speed=60.0, start=datetime(2020, 2, 9, 9, 0))  # replay: the gate day of step 1
    sw.on_plan_message(DoctorMessage(message_id="m", kind="hold_step", hold_weeks=2, created_at=datetime(2026, 9, 27, 18, 0)))
    assert sw.active_plan().steps[1].planned_start == date(2020, 2, 26)
    clock.set(speed=60.0, start=datetime(2020, 1, 10, 9, 0))  # before the plan started: nothing to hold
    sw.on_plan_message(DoctorMessage(message_id="m2", kind="hold_step", hold_weeks=2, created_at=datetime(2020, 1, 10, 9, 0)))
    assert sw.active_plan().steps[0].planned_start == date(2020, 1, 15) and sw.active_plan().steps[1].planned_start == date(2020, 2, 26)
    store.upsert_plan(plan(n_steps=2))  # on the maintenance step there is no step-up to hold
    clock.set(speed=60.0, start=datetime(2020, 3, 1, 9, 0))
    sw.on_plan_message(DoctorMessage(message_id="m3", kind="hold_step", hold_weeks=8, created_at=clock.now()))
    assert [s_.planned_start for s_ in sw.active_plan().steps] == [date(2020, 1, 15), date(2020, 2, 12)]


def test_a_plan_update_keeps_the_green_run_and_the_watch_runs_before_standing(db):
    sw = watch(Sources())
    clock.set(speed=60.0, start=datetime(2020, 2, 18, 7, 5))
    store.upsert_plan(plan(n_steps=2))
    store.set_kv("step_watch:green:p1", '["p1:1"]')
    edited = plan(n_steps=2).model_copy(update={"drug_label": "tirzepatide (edited)"})
    sw.on_plan_message(DoctorMessage(message_id="m", kind="plan_update", plan=edited, created_at=clock.now()))
    assert sw.active_plan().drug_label == "tirzepatide (edited)" and sw.green_weeks(sw.active_plan()) == 1
    from app import main

    src = inspect.getsource(main._evaluate_after_ledger)
    assert src.index("step_watch.run") < src.index("standing.run")
