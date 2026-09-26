"""Scheduler (chris.md step 10): night window, basal nudge (visual at 1 h,
email at 90 min, daily reset), morning report trigger, and from Rounds the
ledger build at night-window end and program evaluation 5 min later (R3,
R8). Clock guard: wall-clock jobs wait for `timedatectl show -p
NTPSynchronized --value` to read yes (polled every 10 s of clock time);
bypassed (clock_synced = True) under IRIN_HW=mock and in replay. Alarms,
stale marking, and re-arm timers never wait. All on clock.py."""

from __future__ import annotations


class Scheduler:
    def __init__(self) -> None:
        self.clock_synced = True

    async def run(self) -> None:
        raise NotImplementedError("step 10: scheduler jobs and the NTP sync guard")
