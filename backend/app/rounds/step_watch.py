"""R10: Step Watch, the weeks after a GLP-1, tirzepatide, or weekly-basal
start. Pure rules over George's numbers (ml/models/nights.py
step_window_metrics and standing_window): nothing is computed here beyond
comparisons and date arithmetic.

Windows (nights keyed by their evening date, like the ledger): baseline = the
14 nights before the plan start with coverage >= 85%, needing 5 such nights
else "baseline thin"; early check on day 3 of the first step (nights 1-3,
labeled limited data); step check on day 7 of each step (days 3-7); step
gate 3 days before each planned step-up (proceed or hold); graduation after
4 consecutive green weeks at the maintenance dose.

Status, evaluated in order: RED at any coverage on a level 2 low, a re-armed
low alarm, or a ketone-risk episode on a can't-eat day; INSUFFICIENT under
70% window coverage (no conclusions; red still sent); AMBER lows (low-point
shift <= -15, near-misses >= 2, TBR > 4%), awareness (a nocturnal low
reported unfelt or not remembered, or two inferred unfelt lows unanswered),
tolerance (rough or can't eat on >= 3 of 5 days), highs (>= 2 ketone-risk
episodes); GREEN otherwise. Holds of 2 / 4 / 8 weeks shift every later
planned start and stack. A card never contains a dose recommendation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from ..contracts import TitrationPlan, TitrationStep

BASELINE_NIGHTS = 14
BASELINE_MIN = 5
BASELINE_COVERAGE = 85.0
EARLY_CHECK_DAY = 3
STEP_CHECK_DAY = 7
GATE_DAYS_BEFORE = 3
GRADUATION_GREEN_WEEKS = 4
SHIFT_FIRES = -15.0
NEAR_MISSES_FIRE = 2
TBR_FIRES = 4.0
TOLERANCE_DAYS = 3
KETONE_HIGHS = 2
COVERAGE_INSUFFICIENT = 70.0
HOLD_WEEKS = (2, 4, 8)


@dataclass
class StepEvaluation:
    kind: str  # early_check | step_check | step_gate | safety | graduation
    status: str  # green | amber | red | insufficient
    flags: list[str]
    metrics: dict[str, Any]
    confidence: dict[str, str]
    headline: str
    step_index: int
    period_start: date
    period_end: date
    tolerance_days: list[dict[str, Any]] = field(default_factory=list)
    nights: list[dict[str, Any]] = field(default_factory=list)
    limited: bool = False  # the early check: labeled limited data
    red_event_key: str | None = None


# ---------------------------------------------------------------- the plan's calendar


def current_step(plan: TitrationPlan, today: date) -> TitrationStep | None:
    """The step whose planned_start is the latest on or before today."""
    started = [s for s in plan.steps if s.planned_start <= today]
    return max(started, key=lambda s: s.planned_start) if started else None


def next_step(plan: TitrationPlan, today: date) -> TitrationStep | None:
    later = [s for s in plan.steps if s.planned_start > today]
    return min(later, key=lambda s: s.planned_start) if later else None


def day_in_step(step: TitrationStep, today: date) -> int:
    """Day 1 is the step's planned start."""
    return (today - step.planned_start).days + 1


def baseline_dates(plan: TitrationPlan) -> tuple[date, date]:
    return plan.started_at - timedelta(days=BASELINE_NIGHTS), plan.started_at - timedelta(days=1)


def due_kinds(plan: TitrationPlan, today: date) -> list[tuple[str, TitrationStep, tuple[date, date]]]:
    """Which cards fall due on `today` (the evening date of the night that just
    closed): (kind, step, (window_start, window_end)) each."""
    if plan.status != "active":
        return []
    step = current_step(plan, today)
    if step is None:
        return []
    due = []
    d = day_in_step(step, today)
    if step.index == 0 and d == EARLY_CHECK_DAY:
        due.append(("early_check", step, (step.planned_start, today)))  # nights 1-3
    if d == STEP_CHECK_DAY:
        due.append(("step_check", step, (step.planned_start + timedelta(days=2), today)))  # days 3-7
    nxt = next_step(plan, today)
    if nxt is not None and today == nxt.planned_start - timedelta(days=GATE_DAYS_BEFORE):
        due.append(("step_gate", step, (today - timedelta(days=6), today)))  # the last 7 nights before the gate
    return due


def apply_hold(plan: TitrationPlan, hold_weeks: int, from_index: int | None = None) -> TitrationPlan:
    """Every planned start after the current (or given) step moves by the hold; holds stack."""
    if hold_weeks not in HOLD_WEEKS:
        raise ValueError(f"hold_weeks must be one of {HOLD_WEEKS}")
    shift = timedelta(weeks=hold_weeks)
    steps = [s.model_copy(update={"planned_start": s.planned_start + shift})
             if (from_index is None or s.index > from_index) else s for s in plan.steps]
    return plan.model_copy(update={"steps": steps})


# ---------------------------------------------------------------- the rules


def baseline_thin(baseline_records) -> bool:
    good = [r for r in baseline_records if r.coverage_pct >= BASELINE_COVERAGE and "stale" not in r.reason_codes]
    return len(good) < BASELINE_MIN


def evaluate_window(plan: TitrationPlan, kind: str, step: TitrationStep, window_records, baseline_records,
                    symptom_checks, injections, recalls, low_events, alarm_events, *, window_readings, window_dates,
                    expected_injections: int | None, alarm_source: str = "measured") -> StepEvaluation:
    from ml.models.nights import standing_window, step_window_metrics

    values, confidence = step_window_metrics(
        window_records, baseline_records, symptom_checks, injections, window_readings=window_readings,
        window_days=len(window_dates), window_dates=window_dates, expected_injections=expected_injections,
        expected_dose_label=step.dose_label, alarm_source=alarm_source)
    values, confidence = dict(values), dict(confidence)
    aware, aware_conf = standing_window(window_records, low_events, recalls, alarm_events=alarm_events,
                                        alarm_source=alarm_source)
    for k in ("unfelt_lows", "inferred_unfelt_unanswered", "answered", "no_answer", "nocturnal_lows", "rearms"):
        values[k] = aware[k]
        confidence[k] = aware_conf[k]
    values["baseline_thin"] = baseline_thin(baseline_records)
    tol = values["tolerance"]
    rough_days = tol["rough"] + tol["cant_eat"]
    level2 = sum(int(r.level2_count or 0) for r in window_records)
    values["level2_lows"] = level2
    confidence["level2_lows"] = "measured"
    ketone = values.get("ketone_risk_episodes") or 0

    flags: list[str] = []
    red_key = None
    # RED, at any coverage
    if level2 > 0:
        flags.append("level2")
    if values["rearms"] > 0:
        flags.append("rearm")
        rearmed = [a for a in alarm_events if (a.rearm_count or 0) > 0]
        red_key = max(rearmed, key=lambda a: a.started_at).event_id if rearmed else None
    if ketone >= 1 and tol["cant_eat"] >= 1:
        flags.append("ketone_risk")
    common = dict(metrics=values, confidence=confidence, step_index=step.index, tolerance_days=[
        {"date": d.isoformat(), "gi": next((c.gi for c in symptom_checks if c.date == d), "missing")} for d in window_dates],
        nights=[{"night_date": r.night_date.isoformat(), "reason_codes": list(r.reason_codes), "code_source": r.code_source,
                 "low_point_mgdl": r.low_point_mgdl, "near_miss_count": r.near_miss_count, "coverage_pct": r.coverage_pct}
                for r in window_records], limited=(kind == "early_check"))
    period = (window_dates[0], window_dates[-1])
    label = f"Step {step.index + 1} ({step.dose_label})"
    if flags:
        parts = []
        if level2:
            parts.append(f"{level2} level 2 low{'s' if level2 > 1 else ''} (under 54)")
        if values["rearms"]:
            parts.append(f"{values['rearms']} re-armed low alarm{'s' if values['rearms'] > 1 else ''}")
        if "ketone_risk" in flags:
            parts.append("a ketone-risk episode on a can't-eat day")
        return StepEvaluation(kind="safety", status="red", flags=flags, headline=f"{label}: {'; '.join(parts)}.",
                              period_start=period[0], period_end=period[1], red_event_key=red_key, **common)
    coverage = values.get("coverage_pct")
    if coverage is None or coverage < COVERAGE_INSUFFICIENT:
        cov = f"{coverage:.0f}%" if coverage is not None else "unknown"
        return StepEvaluation(kind=kind, status="insufficient", flags=[],
                              headline=f"{label}: sensor coverage {cov}, under 70%; no conclusion this window.",
                              period_start=period[0], period_end=period[1], **common)
    shift, near, tbr = values.get("low_point_shift"), values["near_misses"], values.get("tbr_pct")
    if (shift is not None and shift <= SHIFT_FIRES) or near >= NEAR_MISSES_FIRE or (tbr is not None and tbr > TBR_FIRES):
        flags.append("lows")
    if values["unfelt_lows"] >= 1 or values["inferred_unfelt_unanswered"] >= 2:
        flags.append("awareness")
    if rough_days >= TOLERANCE_DAYS:
        flags.append("tolerance")
    if ketone >= KETONE_HIGHS:
        flags.append("highs")
    if values["baseline_thin"]:
        flags.append("baseline_thin")
    if values["adherence"].get("dose_mismatch"):
        flags.append("dose_mismatch")
    if (values["adherence"].get("missed") or 0) > 0:
        flags.append("missed_injection")
    status = "amber" if any(f in flags for f in ("lows", "awareness", "tolerance", "highs")) else "green"
    days = f"days {day_in_step(step, window_dates[0])} to {day_in_step(step, window_dates[-1])}"
    if kind == "early_check":
        days = f"days 1 to {len(window_dates)}, limited data"
    sentences = [f"{label}, {days}."]
    if shift is not None:
        sentences.append(f"Overnight low point {'down' if shift < 0 else 'up'} {abs(shift):.0f} mg/dL from baseline, "
                         f"{near} near-miss{'es' if near != 1 else ''}, time below range {tbr:.1f}%.")
    else:
        sentences.append(f"{near} near-miss{'es' if near != 1 else ''}, time below range {tbr:.1f}%; no baseline low point to compare.")
    if values["unfelt_lows"]:
        sentences.append(f"{values['unfelt_lows']} low{'s' if values['unfelt_lows'] > 1 else ''} not remembered.")
    if rough_days:
        sentences.append(f"Rough stomach {rough_days} of {len(window_dates)} days.")
    if ketone:
        sentences.append(f"{ketone} ketone-risk episode{'s' if ketone > 1 else ''}.")
    return StepEvaluation(kind=kind, status=status, flags=flags, headline=" ".join(sentences),
                          period_start=period[0], period_end=period[1], **common)


def graduated(green_weeks: int) -> bool:
    return green_weeks >= GRADUATION_GREEN_WEEKS


# ---------------------------------------------------------------- the service (state, check-ins, the due cards)

import logging as _logging
from typing import Callable

from .. import store as _store
from ..clock import clock as _clock
from ..contracts import DoctorMessage, LowEventRecall, SymptomCheck, Treatment
from . import noise as _noise
from .cards import CardSender, assemble
from .nights_adapter import NightsAdapter

_log = _logging.getLogger("irin.rounds.step_watch")


@dataclass
class StepWatch:
    adapter: NightsAdapter
    sender: CardSender
    device_id: str
    is_demo: Callable[[], bool] = lambda: False
    brain_only: Callable[[], bool] = lambda: False
    on_plan_state: Callable[[dict], None] | None = None  # plan_state broadcast
    on_checkin_due: Callable[[dict], None] | None = None  # symptom_check_due broadcast
    green_weeks: dict[str, int] = field(default_factory=dict)  # plan_id -> consecutive green step checks

    # --- the plan ---

    def active_plan(self) -> TitrationPlan | None:
        demo = self.is_demo()
        for p in _store.select_plans():
            if p.status == "active" and p.is_demo == demo:
                return p
        return None

    def plan_state(self) -> dict[str, Any]:
        plan = self.active_plan()
        if plan is None:
            return {"active": False}
        today = _clock.now().date()
        step = current_step(plan, today)
        nxt = next_step(plan, today)
        return {"active": True, "plan_id": plan.plan_id, "drug_label": plan.drug_label, "status": plan.status,
                "step_index": step.index if step else None, "dose_label": step.dose_label if step else None,
                "day_in_step": day_in_step(step, today) if step else None,
                "next_step_on": nxt.planned_start.isoformat() if nxt else None,
                "green_weeks": self.green_weeks.get(plan.plan_id, 0), "is_demo": plan.is_demo}

    def _changed(self) -> None:
        if self.on_plan_state is not None:
            try:
                self.on_plan_state(self.plan_state())
            except Exception:
                _log.exception("plan_state observer failed")

    def on_plan_message(self, msg: DoctorMessage) -> None:
        """R9's confirm hook: applies plan_create / plan_update / proceed / hold_step / end_watch."""
        demo = self.is_demo()
        if msg.kind in ("plan_create", "plan_update"):
            plan = msg.plan.model_copy(update={"status": "active", "is_demo": demo})
            _store.upsert_plan(plan)
            self.green_weeks.pop(plan.plan_id, None)
            _log.info("step watch %s: %s", plan.plan_id, msg.kind)
        else:
            plan = self.active_plan()
            if plan is None:
                _log.warning("%s without an active watch: nothing to apply", msg.kind)
                return
            if msg.kind == "hold_step":
                step = current_step(plan, _clock.now().date())
                _store.upsert_plan(apply_hold(plan, int(msg.hold_weeks), from_index=step.index if step else None))
            elif msg.kind == "end_watch":
                _store.upsert_plan(plan.model_copy(update={"status": "ended"}))
            # proceed: the gate stays as planned; nothing to change
        self._changed()

    # --- the daily stomach check-in (one tap; missing is no data, never fine) ---

    def checkin_due(self, today: date | None = None) -> bool:
        plan = self.active_plan()
        if plan is None:
            return False
        today = today or _clock.now().date()
        return _store.select_symptom_check(today) is None

    def checkin(self, gi: str, today: date | None = None) -> SymptomCheck:
        today = today or _clock.now().date()
        check = SymptomCheck(date=today, gi=gi, is_demo=self.is_demo())  # type: ignore[arg-type]
        _store.upsert_symptom_check(check)
        return check

    def checkin_status(self) -> dict[str, Any]:
        today = _clock.now().date()
        done = _store.select_symptom_check(today)
        return {"symptom_check_due": self.checkin_due(today), "symptom_check_today": done.gi if done else None}

    # --- the cards ---

    def _inputs(self, plan: TitrationPlan, window: tuple[date, date]) -> dict[str, Any]:
        demo = self.is_demo()
        w0, w1 = window
        b0, b1 = baseline_dates(plan)
        s = self.adapter.settings
        from .nights_adapter import night_window

        start = night_window(w0, s.night_window_start, s.night_window_end)[0]
        end = night_window(w1, s.night_window_start, s.night_window_end)[1]
        dates = [w0 + timedelta(days=i) for i in range((w1 - w0).days + 1)]
        day_start = datetime.combine(w0, time.min)
        day_end = datetime.combine(w1 + timedelta(days=1), time.min)
        return {
            "window_records": [r for r in _store.select_night_records(w0, w1) if r.is_demo == demo],
            "baseline_records": [r for r in _store.select_night_records(b0, b1) if r.is_demo == demo],
            "symptom_checks": [c for c in _store.select_symptom_checks(w0, w1) if c.is_demo == demo],
            "injections": [t for t in self.adapter.treatments_for(day_start - timedelta(days=7), day_end) if t.kind == "glp1_dose"],
            "recalls": [r for r in _store.select_recalls(w0) if r.is_demo == demo],
            "low_events": [e for e in _store.select_low_events(w0, w1) if e.is_demo == demo],
            "alarm_events": [a for a in _store.select_alarm_events(start, end) if a.is_demo == demo],
            "window_readings": [r for r in self.adapter.readings_for(day_start, day_end)
                                if not r.is_stale and day_start <= r.timestamp < day_end],
            "window_dates": dates,
            # weekly drugs: one shot per full week of the fetched span (the window plus the 7 days before it)
            "expected_injections": (len(dates) + 7) // 7 if plan.drug_class in ("glp1", "gip_glp1", "weekly_basal") else None,
        }

    def evaluate(self, plan: TitrationPlan, kind: str, step: TitrationStep, window: tuple[date, date]) -> StepEvaluation:
        inp = self._inputs(plan, window)
        return evaluate_window(plan, kind, step, inp["window_records"], inp["baseline_records"], inp["symptom_checks"],
                               inp["injections"], inp["recalls"], inp["low_events"], inp["alarm_events"],
                               window_readings=inp["window_readings"], window_dates=inp["window_dates"],
                               expected_injections=inp["expected_injections"],
                               alarm_source="inferred" if self.brain_only() else "measured")

    async def run(self, today: date | None = None) -> list[dict[str, Any]]:
        """The due cards for `today` (the evening date of the night that just closed):
        evaluated, budgeted, sent. Red safety cards go out on their own key."""
        plan = self.active_plan()
        if plan is None:
            return []
        today = today or _clock.now().date()
        out = []
        history = _noise.history_from_store(_store.select_cards(limit=500))
        for kind, step, window in due_kinds(plan, today):
            ev = self.evaluate(plan, kind, step, window)
            key = ev.red_event_key or f"{plan.plan_id}:{step.index}:red" if ev.status == "red" else f"{plan.plan_id}:{step.index}"
            verdict = _noise.allow(ev.kind, "step_watch", ev.status, _clock.now(), history, active_watch=True, event_key=key)
            entry = {"kind": ev.kind, "status": ev.status, "flags": ev.flags, "headline": ev.headline,
                     "step_index": step.index, "budget": verdict.reason, "sent": None}
            if ev.kind == "step_check":
                self.green_weeks[plan.plan_id] = self.green_weeks.get(plan.plan_id, 0) + 1 if ev.status == "green" else 0
            if verdict.allowed:
                card = assemble(program="step_watch", kind=ev.kind, status=ev.status, flags=ev.flags, metrics=ev.metrics,
                                confidence=ev.confidence, period_start=ev.period_start, period_end=ev.period_end,
                                headline=ev.headline, device_id=self.device_id, is_demo=plan.is_demo,
                                source="irin_brain" if self.brain_only() else "irin_bedside", nights=ev.nights,
                                tolerance_days=ev.tolerance_days, plan_id=plan.plan_id, step_index=step.index,
                                resource_categories=["gi_side_effect_education"] if "tolerance" in ev.flags else [])
                try:
                    entry["sent"] = await self.sender.send(card, event_key=key)
                except Exception:
                    _log.exception("step watch card failed")
                    entry["sent"] = {"status": "failed"}
                history.append(_noise.Sent(ev.kind, "step_watch", ev.status, _clock.now(), key))
            out.append(entry)
        last = plan.steps[-1] if plan.steps else None
        if last and current_step(plan, today) is last and graduated(self.green_weeks.get(plan.plan_id, 0)) and plan.status == "active":
            _store.upsert_plan(plan.model_copy(update={"status": "graduated"}))
            out.append({"kind": "graduation", "status": "green", "step_index": last.index, "budget": "sent", "sent": None,
                        "headline": f"Four green weeks at {last.dose_label}: the watch is complete."})
            self._changed()
        return out
