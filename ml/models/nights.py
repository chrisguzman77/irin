"""The ONE night-classification and metrics module (george.md step 4),
imported by the backend's ledger, Standing Cards, and Step Watch AND by the
validation scripts. Pure functions, numpy only, no I/O. Every metric is
computed here and nowhere else. A value that cannot be computed is None,
never zero; every value carries its confidence label in a parallel dict."""

from __future__ import annotations


def classify_night(readings, treatments, alarm_events, presence, night_window) -> tuple[list[str], str]:
    """-> (reason_codes, code_source). Codes: late_meal, late_correction, basal_late,
    basal_missed, exercise, treated_low, stale, away, clean. code_source: logged |
    inferred | unknown (basal_late, exercise, away are unknown on history)."""
    raise NotImplementedError("george.md step 4.1")


def night_metrics(readings, alarm_events, night_window) -> dict:
    """coverage_pct, rise_mgdl, dawn_rise_mgdl, low_point_mgdl, tbr_pct, minutes_below_70,
    auc_below_70, near_miss_count, level2_count, ketone_risk_episodes."""
    raise NotImplementedError("george.md step 4.2")


def low_events(readings, treatments, alarm_events, night_window) -> list[dict]:
    """One LowEvent per nocturnal low (under 70 confirmed by 2 readings)."""
    raise NotImplementedError("george.md step 4.3")


def standing_window(night_records, low_events, recalls) -> tuple[dict, dict]:
    """14-night metrics + a parallel confidence dict (measured / reported / inferred)."""
    raise NotImplementedError("george.md step 4.4")


def step_window_metrics(window_records, baseline_records, symptom_checks, injections) -> dict:
    """low_point_shift, tbr_pct, near_misses, coverage_pct, baseline_nights, tolerance counts,
    adherence + dose-mismatch flag, ketone_risk_episodes."""
    raise NotImplementedError("george.md step 4.5")
