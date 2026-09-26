"""R8: Standing Cards as pure functions, evaluate(night_records, low_events,
recalls, settings) -> (status, flags, metrics, confidence), no I/O. Basal
Check (14 nights, >= 5 clean, median rise beyond +/-30, >= 70% same direction;
too-high direction at >= 3 near-misses; excluded nights listed), Hypo Response
(>= 2 escalated warnings, >= 1 re-arm, median ack > 5 min on home events, any
reported unfelt low), Follow-up (14 nights before vs days 1-7 and 1-14 after,
>= 3 clean nights per side, else "not enough data yet")."""

from __future__ import annotations


def evaluate_basal_check(night_records, low_events, recalls, settings):
    raise NotImplementedError("R8: Basal Check")


def evaluate_hypo_response(night_records, low_events, recalls, alarm_events, settings):
    raise NotImplementedError("R8: Hypo Response")


def evaluate_follow_up(before, after, settings):
    raise NotImplementedError("R8: Follow-up")
