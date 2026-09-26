"""R8: the noise budget, in ONE place (invariant 10). One Standing Card per
type per patient per 14 days; one step check and one step gate per step; green
never interrupts (weekly digest); red bypasses everything but is deduplicated
per event and capped at one per type per 12 hours; a Step Watch suspends
Basal Check and absorbs Hypo Response into its red safety card; nothing from
stale nights or insufficient windows; never two programs on the same day."""

from __future__ import annotations


def allow(card, history) -> bool:
    raise NotImplementedError("R8: noise budget")
