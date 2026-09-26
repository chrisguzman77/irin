"""R10: Step Watch. TitrationPlan windows (baseline 14 nights >= 85% coverage,
>= 5 nights; early check day 3; step check day 7 over days 3-7; gate 3 days
before a step; graduation after 4 green weeks), status in order RED ->
INSUFFICIENT (< 70%) -> AMBER (lows / awareness / tolerance / highs) ->
GREEN; holds 2/4/8 weeks shift every later step and stack; one check and one
gate per step (red exempt). The demo amber worked example is in chris.md."""

from __future__ import annotations


def evaluate_window(plan, window_records, baseline_records, symptom_checks, injections, recalls):
    raise NotImplementedError("R10: Step Watch")
