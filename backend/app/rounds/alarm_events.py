"""R2: the AlarmEvent recorder. An observer on alarm.py's on_transition hook
that writes one AlarmEvent per episode (tier, started_at, acknowledged_at,
ack_source, escalated, rearm_count, crossed_actual, presence_during, is_demo).
presence_during is the RAW radar aggregated ONCE, here (decision B7): sample
hal.get_presence() every 30 s of clock time; home if a majority detect OR any
detection within 2 min of alarm start; away if zero detections; unknown
otherwise. Observes only; never calls into alarm.py."""

from __future__ import annotations


class AlarmEventRecorder:
    def __call__(self, transition) -> None:  # registered via engine.on_transition
        raise NotImplementedError("R2: AlarmEvent recorder")
