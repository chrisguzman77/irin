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
On the False -> True transition `on_synced` runs (main.py re-anchors clock.py
to the corrected wall time in live mode) and the poll stops.

A job fires at the first synced tick within CATCHUP_HOURS after its time
(a Pi that boots at 22:30 never runs the 07:00 morning report at bedtime);
same-tick jobs run in "HH:MM" order; a job that raises is done for the day
and never re-fired (a half-done job is never repeated).
"""

from __future__ import annotations

import asyncio
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
BASAL_NUDGE_MAX_HOURS = 8  # the ladder resets this long after basal time (or when logged)
CATCHUP_HOURS = 3  # a job missed by more than this waits for tomorrow

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
    at: "str | Callable[[], str]"  # "HH:MM" wall-clock time, daily; a callable is read on every tick
    fn: JobFn
    last_fired: date | None = None

    def at_hhmm(self) -> str:
        return self.at() if callable(self.at) else self.at


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
    held: bool = False  # R12: set while the seek's catch-up replays mornings; no job fires meanwhile
    jobs: list[Job] = field(default_factory=list)
    nudge: BasalNudge = field(default_factory=BasalNudge)
    basal_logged_today: Callable[[date], bool] = lambda d: False  # wired to the store in main.py
    mailer: Callable[[str, str], None] | None = None  # (subject, body); None = no email path yet
    on_nudge: Callable[[BasalNudge], None] | None = None
    on_synced: Callable[[], None] | None = None  # main.py: clock.resync() in live mode

    def __post_init__(self) -> None:
        if self.sync_check is None:
            self.clock_synced = True

    # --- registration ---

    def register(self, name: str, at: "str | Callable[[], str]", fn: JobFn) -> None:
        """A daily wall-clock job at "HH:MM" (or a callable returning it, so a
        settings change moves the job); fires once per date, only while synced."""
        parse_hhmm(at() if callable(at) else at)
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
        """Poll the guard until it reads yes; on that transition run on_synced."""
        if self.sync_check is not None and not self.clock_synced:
            self.clock_synced = bool(self.sync_check())
            if self.clock_synced:
                log.info("wall clock synced; wall-clock jobs released")
                if self.on_synced is not None:
                    self.on_synced()
        return self.clock_synced

    def tick(self, refresh: bool = True) -> list[str]:
        """Returns the names of the jobs fired on this tick. run() polls the
        guard in a thread first and calls tick(refresh=False)."""
        if refresh:
            self.refresh_sync()
        if not self.clock_synced or self.held:
            return []  # held: the replay catch-up is moving the clock morning by morning (R12)
        now = clock.now()
        fired: list[str] = []
        for job in sorted(self.jobs, key=lambda j: parse_hhmm(j.at_hhmm())):
            t = parse_hhmm(job.at_hhmm())
            due = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
            if due <= now < due + timedelta(hours=CATCHUP_HOURS) and job.last_fired != now.date():
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
        if now < due:
            due -= timedelta(days=1)  # a basal time late in the evening runs its ladder past midnight
        late = now - due
        if late >= timedelta(hours=BASAL_NUDGE_MAX_HOURS) or self.basal_logged_today(due.date()):
            self._set_nudge("none", None)  # logged, or the ladder expired: daily reset
            return
        if late >= timedelta(minutes=BASAL_EMAIL_MIN):
            self._set_nudge("email", due)
            if self.nudge.emailed_on != due.date() and self.mailer is not None:
                self.nudge.emailed_on = due.date()
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
                if not self.clock_synced:
                    await asyncio.to_thread(self.refresh_sync)  # timedatectl never blocks the loop
                self.tick(refresh=False)
            except Exception:
                log.exception("scheduler tick failed")
            await clock.sleep(GUARD_POLL_CLOCK_SECONDS)


def basal_logged_on(treatments: list[Treatment], day: date) -> bool:
    """A basal logged on `day` or later (a 23:00 basal logged at 00:10 still
    clears the ladder for the night it was due)."""
    return any(t.kind == "basal" and t.timestamp.date() >= day for t in treatments)
