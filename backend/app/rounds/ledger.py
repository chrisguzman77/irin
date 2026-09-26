"""R3: the night ledger. At night-window end (clock.py) write the NightRecord
for the night just ended; every metric and reason code comes from
ml/models/nights.py (never re-implemented here); idempotent by night_date."""

from __future__ import annotations


def build_night(night_date) -> None:
    raise NotImplementedError("R3: night ledger")
