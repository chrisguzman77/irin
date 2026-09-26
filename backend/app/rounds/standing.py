"""R8: Standing Cards as pure functions, no I/O, so the same code runs over
history for validation. Every number comes from ml/models/nights.py
(standing_window) with its confidence label; the rules here only compare
those numbers with thresholds. Thresholds are starting values (config, not
code), tuned by George against the real history: checkpoint 6 moved the
"possibly too high" near-miss trigger from 3 to 5 in 14 nights.

Basal Check (detect): over the last 14 nights, at least 5 clean nights, a
median overnight rise on clean nights beyond +/-30 mg/dL with at least 70%
of clean nights in the same direction (rise_high = basal possibly too low),
or near-misses at the trigger count (rise_low = basal possibly too high);
never from stale nights; every excluded night listed with its reason.
Hypo Response (safety, red): in 14 days at least 2 escalated warnings, or a
re-arm, or a median acknowledgement above 5 minutes on home events, or any
nocturnal low reported unfelt; carries the patient-reported glucagon status.
Follow-up (verify): the 14 nights before a confirmed dose change against
days 1-7 and 1-14 after, at least 3 clean nights on each side, otherwise
"not enough data yet". A card never contains a dose recommendation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..contracts import Settings


@dataclass(frozen=True)
class Thresholds:
    window_nights: int = 14
    clean_nights_min: int = 5
    rise_min_mgdl: float = 30.0  # beyond +/-30 fires
    same_direction_min: float = 0.70
    near_miss_too_high: int = 5  # checkpoint 6 (George): 5, not the plan's 3
    escalated_warnings_min: int = 2
    rearms_min: int = 1
    ack_median_min: float = 5.0  # minutes, home events only
    follow_up_clean_per_side: int = 3


@dataclass
class Evaluation:
    kind: str
    status: str  # green | amber | red | insufficient
    flags: list[str]
    metrics: dict[str, Any]
    confidence: dict[str, str]
    headline: str
    nights: list[dict[str, Any]] = field(default_factory=list)
    excluded_counts: dict[str, int] = field(default_factory=dict)
    period_start: date | None = None
    period_end: date | None = None


def _window_values(night_records, low_events, recalls, alarm_events, alarm_source: str) -> tuple[dict, dict]:
    from ml.models.nights import standing_window

    values, confidence = standing_window(night_records, low_events, recalls, alarm_events=alarm_events, alarm_source=alarm_source)
    return dict(values), dict(confidence)


def _night_rows(night_records) -> list[dict[str, Any]]:
    return [{"night_date": r.night_date.isoformat(), "reason_codes": list(r.reason_codes), "code_source": r.code_source,
             "rise_mgdl": r.rise_mgdl, "low_point_mgdl": r.low_point_mgdl, "coverage_pct": r.coverage_pct}
            for r in night_records]


def _excluded_counts(values: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for n in values.get("excluded_nights") or []:
        for reason in n.get("reasons") or ["unknown"]:
            counts[reason] = counts.get(reason, 0) + 1
    return counts


def _period(night_records) -> tuple[date | None, date | None]:
    dates = sorted(r.night_date for r in night_records)
    return (dates[0], dates[-1]) if dates else (None, None)


def evaluate_basal_check(night_records, low_events, recalls, thresholds: Thresholds = Thresholds(), *,
                         alarm_events=(), alarm_source: str = "measured") -> Evaluation:
    values, confidence = _window_values(night_records, low_events, recalls, alarm_events, alarm_source)
    start, end = _period(night_records)
    clean, rise, share = values["clean_nights"], values["rise_median_clean"], values["same_direction_share"]
    near = values["near_misses"]
    common = dict(kind="basal_check", metrics=values, confidence=confidence, nights=_night_rows(night_records),
                  excluded_counts=_excluded_counts(values), period_start=start, period_end=end)
    if clean < thresholds.clean_nights_min:
        return Evaluation(status="insufficient", flags=[],
                          headline=f"{clean} clean nights of {values['nights']}; {thresholds.clean_nights_min} are needed before a basal check.",
                          **common)
    if rise is None:
        return Evaluation(status="insufficient", flags=[],
                          headline=f"{clean} clean nights of {values['nights']}, but no overnight rise could be measured.", **common)
    # near-misses count on clean nights only (never from stale or excluded nights, invariant 9)
    near_clean = sum(int(r.near_miss_count or 0) for r in night_records if r.reason_codes == ["clean"] and r.coverage_pct >= 85)
    values["near_misses_clean"] = near_clean
    confidence["near_misses_clean"] = confidence.get("near_misses", alarm_source)
    flags: list[str] = []
    if abs(rise) > thresholds.rise_min_mgdl and (share or 0) >= thresholds.same_direction_min:
        flags.append("rise_high" if rise > 0 else "rise_low")
    if near_clean >= thresholds.near_miss_too_high and not flags:
        flags.append("rise_low")  # possibly too high: lows nearly happened
    same = round((share or 0) * clean)
    direction = "rose" if rise > 0 else "fell"
    if flags:
        headline = (f"{clean} clean nights of {values['nights']}; median overnight rise {rise:+.0f} mg/dL, "
                    f"{same} of {clean} in the same direction; {near_clean} near-misses on clean nights.")
        return Evaluation(status="amber", flags=flags, headline=headline, **common)
    headline = (f"{clean} clean nights of {values['nights']}; glucose {direction} a median {abs(rise):.0f} mg/dL overnight, "
                f"within the band; {near_clean} near-misses on clean nights.")
    return Evaluation(status="green", flags=[], headline=headline, **common)


def evaluate_hypo_response(night_records, low_events, recalls, alarm_events, settings: Settings,
                           thresholds: Thresholds = Thresholds(), *, alarm_source: str = "measured") -> Evaluation:
    values, confidence = _window_values(night_records, low_events, recalls, alarm_events, alarm_source)
    start, end = _period(night_records)
    flags: list[str] = []
    if values["escalated_warnings"] >= thresholds.escalated_warnings_min:
        flags.append("lows")
    if values["rearms"] >= thresholds.rearms_min:
        flags.append("rearm")
    ack = values["median_ack_min"]
    if ack is not None and ack > thresholds.ack_median_min:
        flags.append("lows") if "lows" not in flags else None
    if values["unfelt_lows"] >= 1:
        flags.append("awareness")
    if any((r.level2_count or 0) > 0 for r in night_records):
        flags.append("level2")
    metrics = {**values, "glucagon_on_hand": settings.glucagon_on_hand,
               "glucagon_expiry": settings.glucagon_expiry.isoformat() if settings.glucagon_expiry else None}
    confidence = {**confidence, "glucagon_on_hand": "reported", "glucagon_expiry": "reported"}
    common = dict(kind="hypo_response", metrics=metrics, confidence=confidence, nights=_night_rows(night_records),
                  excluded_counts=_excluded_counts(values), period_start=start, period_end=end)
    red = any(f in flags for f in ("lows", "rearm", "awareness"))
    if red:
        parts = [f"{values['escalated_warnings']} escalated warnings", f"{values['rearms']} re-arms"]
        if ack is not None:
            parts.append(f"median acknowledgement {ack:.0f} min")
        parts.append(f"{values['unfelt_lows']} of {values['answered']} answered lows reported unfelt")
        return Evaluation(status="red", flags=flags, headline="; ".join(parts) + f" in {values['nights']} nights.", **common)
    answered, unanswered = values["answered"], values["no_answer"]
    ack_text = f"median acknowledgement {ack:.0f} min" if ack is not None else "no acknowledgement time measured"
    rate = values["unfelt_low_rate"]
    unfelt = f"{values['unfelt_lows']} unfelt ({rate:.0%})" if rate is not None else "no answers to rate"
    # the plan's wording: the rate divides by answered, and "no answer" stands beside it, never inside it
    return Evaluation(status="green", flags=flags,
                      headline=(f"{values['nocturnal_lows']} nocturnal lows in {values['nights']} nights, {answered} answered, "
                                f"{unfelt}, {unanswered} no answer; {ack_text}."), **common)


def evaluate_follow_up(before, after_7, after_14, thresholds: Thresholds = Thresholds(), *, alarm_source: str = "measured") -> Evaluation:
    """Before a confirmed dose change versus after it: a comparison of the same
    computed numbers, never a judgement of the dose."""
    b_vals, b_conf = _window_values(before, [], [], (), alarm_source)
    a7_vals, _ = _window_values(after_7, [], [], (), alarm_source)
    a14_vals, a_conf = _window_values(after_14, [], [], (), alarm_source)
    start, end = _period(list(before) + list(after_14))
    metrics = {"before_nights": b_vals["nights"], "before_clean_nights": b_vals["clean_nights"],
               "before_rise_median": b_vals["rise_median_clean"], "after7_clean_nights": a7_vals["clean_nights"],
               "after7_rise_median": a7_vals["rise_median_clean"], "after14_clean_nights": a14_vals["clean_nights"],
               "after14_rise_median": a14_vals["rise_median_clean"],
               "excluded_nights": (b_vals["excluded_nights"] or []) + (a14_vals["excluded_nights"] or [])}
    confidence = {k: (a_conf["clean_nights"] if k.startswith("after") else b_conf["clean_nights"])
                  for k in metrics if k != "excluded_nights"}
    common = dict(kind="follow_up", metrics=metrics, confidence=confidence, nights=_night_rows(list(before) + list(after_14)),
                  excluded_counts=_excluded_counts(metrics), period_start=start, period_end=end)
    need = thresholds.follow_up_clean_per_side
    if b_vals["clean_nights"] < need or a14_vals["clean_nights"] < need or metrics["after14_rise_median"] is None \
            or metrics["before_rise_median"] is None:
        return Evaluation(status="insufficient", flags=[], headline="Not enough data yet: fewer than "
                          f"{need} clean nights on one side of the change.", **common)
    delta = metrics["after14_rise_median"] - metrics["before_rise_median"]
    metrics["rise_change"] = delta
    confidence["rise_change"] = a_conf["clean_nights"]
    status = "green" if abs(metrics["after14_rise_median"]) <= thresholds.rise_min_mgdl else "amber"
    return Evaluation(status=status, flags=[] if status == "green" else ["rise_high" if metrics["after14_rise_median"] > 0 else "rise_low"],
                      headline=(f"Median overnight rise {metrics['before_rise_median']:+.0f} mg/dL before the change, "
                                f"{metrics['after14_rise_median']:+.0f} after ({a14_vals['clean_nights']} clean nights)."),
                      **common)
