"""Step 10 check: with the guard reporting false no scheduler job fires and
the snapshot flag is false; true releases them. Plus the basal nudge ladder
(visual at 60 min, email at 90 min, daily reset) and display modes driven by
backend state."""

from datetime import date, datetime, timedelta

import pytest

from app.clock import clock
from app.contracts import Settings, Treatment
from app import scheduler as scheduler_mod
from app.scheduler import Scheduler, basal_logged_on, timedatectl_synced

T0 = datetime(2020, 1, 1, 6, 50)


@pytest.fixture
def at_t0():
    clock.set(speed=60.0, start=T0)
    yield
    clock.reset()


def test_guard_false_holds_every_job_and_true_releases_them(at_t0):
    synced = {"v": False}
    fired = []
    s = Scheduler(Settings(), sync_check=lambda: synced["v"])
    s.register("morning_report", "07:00", lambda d: fired.append(("report", d)))
    s.register("ledger", "07:00", lambda d: fired.append(("ledger", d)))
    assert s.clock_synced is False
    clock.advance(20 * 60)  # 07:10: both jobs are due
    for _ in range(10):
        assert s.tick() == []
    assert fired == [] and s.clock_synced is False  # the snapshot flag stays false
    synced["v"] = True
    assert sorted(s.tick()) == ["ledger", "morning_report"] and s.clock_synced is True
    assert fired == [("report", date(2020, 1, 1)), ("ledger", date(2020, 1, 1))]
    assert s.tick() == []  # once per day
    clock.advance(24 * 3600)
    assert sorted(s.tick()) == ["ledger", "morning_report"]  # and again the next day


def test_mock_and_replay_bypass_the_guard(at_t0):
    s = Scheduler(Settings())  # no sync_check = bypass
    assert s.clock_synced is True
    s.register("j", "06:55", lambda d: None)
    clock.advance(10 * 60)
    assert s.tick() == ["j"]


def test_a_failing_job_never_stops_the_others(at_t0):
    s = Scheduler(Settings())
    fired = []

    def boom(d):
        raise RuntimeError("x")

    s.register("bad", "06:50", boom)
    s.register("good", "06:50", lambda d: fired.append(d))
    assert s.tick() == ["good"] and fired == [date(2020, 1, 1)]


def test_relative_timers_never_wait_for_the_guard():
    """The guard is the scheduler's alone: the alarm engine, staleness, and
    re-arm timers live on clock.py and know nothing about it."""
    import inspect

    from app import alarm, datasource

    assert "clock_synced" not in inspect.getsource(alarm)
    assert "clock_synced" not in inspect.getsource(datasource.nightscout)


def test_basal_nudge_ladder_and_daily_reset(at_t0):
    settings = Settings(basal_time="21:30")
    logged = {"v": False}
    mails = []
    levels = []
    s = Scheduler(settings)
    s.basal_logged_today = lambda d: logged["v"]
    s.mailer = lambda subject, body: mails.append(subject)
    s.on_nudge = lambda n: levels.append(n.level)
    clock.set(speed=60.0, start=datetime(2020, 1, 1, 21, 0))
    s.tick()
    assert s.nudge.level == "none"
    clock.advance(89 * 60)  # 22:29: 59 min late
    s.tick()
    assert s.nudge.level == "none"
    clock.advance(60)  # 22:30: 60 min late -> visual
    s.tick()
    assert s.nudge.level == "visual" and mails == []
    clock.advance(30 * 60)  # 23:00: 90 min late -> email, once
    s.tick()
    s.tick()
    assert s.nudge.level == "email" and mails == ["Irin: basal not logged"]
    logged["v"] = True  # the basal gets logged
    s.tick()
    assert s.nudge.level == "none"
    assert levels == ["visual", "email", "none"]


def test_no_basal_time_means_no_nudge(at_t0):
    s = Scheduler(Settings())
    clock.advance(24 * 3600)
    s.tick()
    assert s.nudge.level == "none"


def test_display_modes_from_backend_state():
    s = Scheduler(Settings())  # night 22:00-07:00, morning for 2 h after
    assert s.display_mode(datetime(2020, 1, 1, 23, 30)) == "night"
    assert s.display_mode(datetime(2020, 1, 2, 3, 0)) == "night"
    assert s.display_mode(datetime(2020, 1, 2, 7, 0)) == "morning"
    assert s.display_mode(datetime(2020, 1, 2, 8, 59)) == "morning"
    assert s.display_mode(datetime(2020, 1, 2, 9, 0)) == "detail"
    assert s.display_mode(datetime(2020, 1, 2, 21, 59)) == "detail"


def test_basal_logged_on():
    t = [Treatment(timestamp=datetime(2020, 1, 1, 21, 40), kind="basal", insulin_units=22, confirmed=True)]
    assert basal_logged_on(t, date(2020, 1, 1)) and not basal_logged_on(t, date(2020, 1, 2))


def test_sync_transition_runs_on_synced_once_and_stops_polling(at_t0):
    synced = {"v": False}
    calls = []
    resyncs = []
    s = Scheduler(Settings(), sync_check=lambda: calls.append(1) or synced["v"])
    s.on_synced = lambda: resyncs.append(1)
    s.tick()
    s.tick()
    assert resyncs == [] and len(calls) == 2
    synced["v"] = True
    s.tick()
    assert resyncs == [1] and s.clock_synced
    s.tick()
    s.tick()
    assert resyncs == [1] and len(calls) == 3  # synced: no more polling, no second resync


def test_timedatectl_missing_or_hanging_reads_as_unsynced(monkeypatch):
    def boom(*a, **k):
        raise OSError("no timedatectl")

    monkeypatch.setattr(scheduler_mod.subprocess, "run", boom)
    assert timedatectl_synced() is False


def test_missed_job_waits_for_tomorrow_instead_of_firing_at_boot():
    """Booting at 22:30 never runs the 07:00 morning report at bedtime."""
    clock.set(speed=60.0, start=datetime(2020, 1, 1, 22, 30))
    try:
        fired = []
        s = Scheduler(Settings())
        s.register("morning_report", "07:00", lambda d: fired.append(d))
        assert s.tick() == [] and fired == []
        clock.advance(8 * 3600 + 30 * 60 + 60)  # 07:01 the next day
        assert s.tick() == ["morning_report"] and fired == [date(2020, 1, 2)]
    finally:
        clock.reset()


def test_same_tick_jobs_fire_in_time_order(at_t0):
    order = []
    s = Scheduler(Settings())
    s.register("evaluate", "07:05", lambda d: order.append("evaluate"))
    s.register("ledger", "07:00", lambda d: order.append("ledger"))
    clock.advance(20 * 60)  # 07:10: both due
    assert s.tick() == ["ledger", "evaluate"] and order == ["ledger", "evaluate"]


def test_late_basal_time_keeps_its_ladder_past_midnight_and_resets_after_the_cap():
    settings = Settings(basal_time="23:00")
    mails = []
    s = Scheduler(settings)
    s.mailer = lambda subject, body: mails.append(subject)
    clock.set(speed=60.0, start=datetime(2020, 1, 1, 23, 59))
    try:
        s.tick()
        assert s.nudge.level == "none"
        clock.advance(60)  # 00:00: 60 min late, across midnight
        s.tick()
        assert s.nudge.level == "visual"
        clock.advance(30 * 60)  # 00:30: 90 min late
        s.tick()
        assert s.nudge.level == "email" and mails == ["Irin: basal not logged"]
        clock.advance(6 * 3600 + 29 * 60)  # 06:59: still on the ladder
        s.tick()
        assert s.nudge.level == "email"
        clock.advance(60)  # 07:00: 8 h past basal time, the ladder resets
        s.tick()
        assert s.nudge.level == "none"
        clock.advance(16 * 3600 + 30 * 60)  # 23:30 the next day: a new ladder, no email yet
        s.tick()
        assert s.nudge.level == "none" and mails == ["Irin: basal not logged"]
    finally:
        clock.reset()


def test_basal_logged_after_midnight_clears_the_nights_ladder():
    t = [Treatment(timestamp=datetime(2020, 1, 2, 0, 10), kind="basal", insulin_units=22, confirmed=True)]
    assert basal_logged_on(t, date(2020, 1, 1))
