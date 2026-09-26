"""R7: SignalCard assembly (program, kind, status, flags, the confidence map,
source, headline from code, metrics, nights, excluded_counts, narrative
(template until R13), allowed_actions, resource_categories, is_demo); card_id
derives from (program, kind, period, plan_id, step_index) so re-evaluation
never duplicates; sealed to the paired key and posted via relay_client;
card_sent broadcast."""

from __future__ import annotations


def assemble(**kwargs):
    raise NotImplementedError("R7: card assembly")
