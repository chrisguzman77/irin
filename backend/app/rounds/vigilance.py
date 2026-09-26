"""R14(a): step-week vigilance. For 7 days after each step-up, raise the
predicted-low threshold by WatchOptions.vigilance_offset_mgdl (70 -> 80)
through the settings path only; never touches the actual-low alarm; expires
on its own. The only Rounds code that reaches the alarm threshold path."""

from __future__ import annotations


def effective_predicted_low_threshold(settings, plan, today) -> float:
    raise NotImplementedError("R14a: step-week vigilance")
