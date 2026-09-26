"""Scheduler (chris.md step 10): wall-clock jobs on clock.py behind the NTP
sync guard.

Jobs: the night window (display mode: detail | night | morning), the basal
nudge (visual at 60 min past basal_time with no basal logged, email at 90
min, daily reset), the morning report trigger at night-window end, and from
Rounds the ledger build at night-window end and evaluation 5 min later (R3,
R8), registered by those modules through register().

Clock guard: the Pi has no RTC battery, so on boot the wall clock is
untrusted until NTP syncs. The scheduler polls `timedatectl show -p
NTPSynchronized --value` every 10 s of clock time and holds EVERY wall-clock
job until it reads yes; until then clock_synced is False and the snapshot
carries it so the display and app show "clock not set". Alarms, stale
marking, and re-arm timers never wait (they are relative, on clock.py).
Under IRIN_HW=mock and in replay the guard is bypassed (clock_synced True).
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable

from .clock import clock
from .contracts import Settings, Treatment
from .windows import in_window, parse_hhmm

log = logging.getLogger("irin.scheduler")

GUARD_POLL_CLOCK_SECONDS = 10.0
MORNING_MODE_HOURS = 2  # the morning screen for 2 h after the night window ends
BASAL_VISUAL_MIN = 60
BASAL_EMAIL_MIN = 90

SyncCheck = Callable[[], bool]
JobFn = Callable[[date], None]


def timedatectl_synced() -> bool:
    """The real guard: `timedatectl show -p NTPSynchronized --value` == yes."""
    try:
        out = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() == "yes"
    except (OSError, subprocess.SubprocessError):
        return False


@dataclass
class Job:
    name: str
    at: str  # "HH:MM" wall-clock time, daily
    fn: JobFn
    last_fired: date | None = None


@dataclass
class BasalNudge:
    level: str = "none"  # none | visual | email
    since: datetime | None = None
    emailed_on: date | None = None


@dataclass
class Scheduler:
    settings: Settings
    sync_check: SyncCheck | None = None  # None = bypass (mock / replay): clock_synced True
    clock_synced: bool = False
    jobs: list[Job] = field(default_factory=list)
    nudge: BasalNudge = field(default_factory=BasalNudge)
    basal_logged_today: Callable[[date], bool] = lambda d: False  # wired to the store in main.py
    mailer: Callable[[str, str], None] | None = None  # (subject, body); None = no email path yet
    on_nudge: Callable[[BasalNudge], None] | None = None

    def __post_init__(self) -> None:
        if self.sync_check is None:
            self.clock_synced = True

    # --- registration ---

    def register(self, name: str, at: str, fn: JobFn) -> None:
        """A daily wall-clock job at "HH:MM"; fires once per date, only while synced."""
        parse_hhmm(at)
        self.jobs.append(Job(name, at, fn))

    # --- state the frontends read ---

    def display_mode(self, now: datetime | None = None) -> str:
        """detail | night | morning, from backend state, never local time."""
        now = now or clock.now()
        s = self.settings
        if in_window(now.time(), s.night_window_start, s.night_window_end):
            return "night"
        end = parse_hhmm(s.night_window_end)
        end_today = now.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
        if end_today <= now < end_today + timedelta(hours=MORNING_MODE_HOURS):
            return "morning"
        return "detail"

    # --- the tick (every 10 s of clock time; tests call it directly) ---

    def refresh_sync(self) -> bool:
        if self.sync_check is not None:
            was = self.clock_synced
            self.clock_synced = bool(self.sync_check())
            if self.clock_synced and not was:
                log.info("wall clock synced; wall-clock jobs released")
        return self.clock_synced

    def tick(self) -> list[str]:
        """Returns the names of the jobs fired on this tick."""
        if not self.refresh_sync():
            return []
        now = clock.now()
        fired: list[str] = []
        for job in self.jobs:
            t = parse_hhmm(job.at)
            due = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
            if now >= due and job.last_fired != now.date():
                job.last_fired = now.date()
                try:
                    job.fn(now.date())
                    fired.append(job.name)
                except Exception:  # one job never takes the scheduler down
                    log.exception("job %s failed", job.name)
        self._basal_nudge(now)
        return fired

    def _basal_nudge(self, now: datetime) -> None:
        s = self.settings
        if not s.basal_time:
            return
        t = parse_hhmm(s.basal_time)
        due = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        if now < due or self.basal_logged_today(now.date()):
            self._set_nudge("none", None)  # before basal time, or logged: daily reset
            return
        late = now - due
        if late >= timedelta(minutes=BASAL_EMAIL_MIN):
            self._set_nudge("email", due)
            if self.nudge.emailed_on != now.date() and self.mailer is not None:
                self.nudge.emailed_on = now.date()
                try:
                    self.mailer("Irin: basal not logged", f"No basal logged by {s.basal_time} plus {BASAL_EMAIL_MIN} minutes.")
                except Exception:
                    log.exception("basal nudge email failed")
        elif late >= timedelta(minutes=BASAL_VISUAL_MIN):
            self._set_nudge("visual", due)
        else:
            self._set_nudge("none", None)

    def _set_nudge(self, level: str, since: datetime | None) -> None:
        if self.nudge.level == level:
            return
        self.nudge.level, self.nudge.since = level, since
        if self.on_nudge is not None:
            self.on_nudge(self.nudge)

    async def run(self) -> None:
        while True:
            try:
                self.tick()
            except Exception:
                log.exception("scheduler tick failed")
            await clock.sleep(GUARD_POLL_CLOCK_SECONDS)


def basal_logged_on(treatments: list[Treatment], day: date) -> bool:
    return any(t.kind == "basal" and t.timestamp.date() == day for t in treatments)
