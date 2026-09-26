"""Insulin on board (chris.md step 5): a decay curve over Settings.iob_duration_hours
(default 4 h) that reaches exactly zero. Check: tests/test_iob.py incl.
negative/absurd inputs."""

from __future__ import annotations

from datetime import datetime

from .contracts import Treatment


def iob(treatments: list[Treatment], at: datetime, duration_hours: float = 4.0) -> float:
    raise NotImplementedError("step 5: IOB decay curve")
