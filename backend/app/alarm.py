"""The tiered alarm state machine (chris.md step 4).

States: idle | pending | active | acknowledged | rearmed
Trigger types: predicted_low | actual_low | high | stale

  Idle -> Pending:        N = 2 consecutive forecasts below the predicted-low threshold
                          (85 on the 20th-percentile forecast; a missing forecast resets
                          the count and never clears a warning already on).
  Pending -> Active:      actual value crosses the low threshold, OR 5 min unacknowledged.
  Pending -> Idle:        warning acknowledged (final for the episode: no re-warn until the
                          episode closes), or the forecast back above threshold for 2
                          consecutive forecasts (the episode closes).
  Idle -> Active:         an actual crossing with no warning first.
  Active -> Acknowledged: user ack (device or app).
  Acknowledged -> Rearmed: still below threshold 15 min later; Rearmed behaves as Active;
                          repeats every 15 min until back above threshold.
  Active|Acknowledged|Rearmed -> Idle: 2 consecutive fresh readings at or above the
                          low threshold (the episode closes).
  Escalation:             5 min unacknowledged in Active or Rearmed -> LEDs strobe, tone
                          replayed (same state; observers see escalated=True).
  Acknowledging a WARNING never pre-acknowledges the actual low: the full alarm fires
  fresh and unacknowledged on the crossing (invariant, the named tier test).
  Priority: actual_low > predicted_low > stale > high. A higher tier replaces a lower
  one immediately and does NOT inherit its acknowledgment.
  Stale during a low: the low keeps sounding; the reading's own is_stale is the banner.
  Highs: ONE-SHOT (one chirp, indicator until recrossing); no ack, no re-arm; an
  optional off-by-default reminder every N hours while still high. Highs respect quiet
  hours (the night window). ONLY lows override quiet hours (invariant 3).
  Stale (no low running): one chirp (quiet hours respected), indicator until a fresh reading.

Outputs go through hal (LEDs, sound); presence gating of those outputs is the hal
layer's job (step 9). alarm.py NEVER reads presence and is never edited by Rounds or
Night Buddy work, except R14(a) step-week vigilance, which may only raise
predicted_low_threshold() for 7 days and never touches the actual-low path.

Timers are deadlines on clock.py checked by tick(); nothing here sleeps. The sound
driver loops a tone until stop_sound() (an expectation on hardware/sound.py).
The ONE observer hook, on_transition(callback), hands every transition (and every
escalation, as old == new with escalated=True) to R2's AlarmEvent recorder and B2's
buddy rung; observers only observe.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Literal

from .clock import clock
from .contracts import AlarmState, AlarmStateName, AlarmTrigger, Forecast, Reading, Settings
from .windows import in_window

PENDING_TIMEOUT_MIN = 5
# George's step 3 operating point (Chris confirmed 2026-09-26): predict() returns a
# 20th-percentile forecast, so the predicted-low threshold is 85 mg/dL on that output.
# The actual-low threshold stays Settings.low_threshold (70).
PREDICTED_LOW_THRESHOLD_MGDL = 85.0
ESCALATION_MIN = 5
REARM_MIN = 15
RECOVERY_READINGS = 2
RECOVERY_FORECASTS = 2

AckSource = Literal["device", "app"]
LOW_TIERS = ("predicted_low", "actual_low")


@dataclass(frozen=True)
class Transition:
    old_state: AlarmStateName
    new_state: AlarmStateName
    trigger_type: AlarmTrigger | None
    reading: Reading | None
    at: datetime
    ack_source: AckSource | None = None
    escalated: bool = False  # the 5-minute louder/strobe step (old_state == new_state)


TransitionObserver = Callable[[Transition], None]


class AlarmEngine:
    def __init__(self, settings: Settings, hal=None) -> None:
        self.settings = settings
        if hal is None:
            from hardware.hal import get_hal  # Slavik's boundary; mock under IRIN_HW=mock

            hal = get_hal()
        self.hal = hal
        self._observers: list[TransitionObserver] = []
        self.reset()

    # --- the ONE observer hook (real) ---

    def on_transition(self, callback: TransitionObserver) -> Callable[[], None]:
        """Register an observer; returns an unregister function."""
        self._observers.append(callback)

        def unregister() -> None:
            if callback in self._observers:
                self._observers.remove(callback)

        return unregister

    # --- state ---

    def reset(self) -> None:
        """Idle, episode closed, outputs off. The mode switch calls this."""
        self.state = AlarmState()
        self.last_reading: Reading | None = None
        self._last_fresh: Reading | None = None  # the re-arm decision reads this, so a sensor dropout cannot hide a low
        self._low_forecasts = 0
        self._high_forecasts = 0
        self._recovered = 0
        self._warning_acked = False  # a warning ack is final for the open episode
        self._deadline: datetime | None = None
        self._escalated = False
        self._high_remind_at: datetime | None = None
        self.hal.stop_sound()
        self.hal.set_leds("ambient")

    def predicted_low_threshold(self) -> float:
        """The ONLY place R14(a) vigilance may reach: the predicted-low threshold.
        The actual-low path reads settings.low_threshold directly."""
        return PREDICTED_LOW_THRESHOLD_MGDL

    @property
    def trigger(self) -> AlarmTrigger | None:
        return self.state.trigger_type

    def _low_running(self) -> bool:
        return self.state.state != "idle" and self.trigger in LOW_TIERS

    def _full_running(self) -> bool:
        return self.state.state in ("active", "acknowledged", "rearmed") and self.trigger in LOW_TIERS

    def _quiet_hours(self, now: datetime) -> bool:
        return in_window(now.time(), self.settings.night_window_start, self.settings.night_window_end)

    def _go(self, new_state: AlarmStateName, trigger: AlarmTrigger | None, reading: Reading | None,
            ack_source: AckSource | None = None, escalated: bool = False) -> None:
        old = self.state.state
        now = clock.now()
        if new_state == "idle":
            self.state = AlarmState()
        else:
            same_episode = old != "idle" and self.trigger == trigger
            started = self.state.started_at if same_episode and self.state.started_at else now
            acked = now if new_state == "acknowledged" else None
            self.state = AlarmState(state=new_state, trigger_type=trigger, started_at=started, acknowledged_at=acked)
        t = Transition(old, new_state, trigger, reading, now, ack_source=ack_source, escalated=escalated)
        for cb in list(self._observers):
            cb(t)

    # --- outputs (hal only; presence gating lives in the hal layer) ---

    def _warning_outputs(self) -> None:
        self.hal.set_leds("warning")
        self.hal.play_sound("alarm_soft", self.settings.volume)

    def _full_outputs(self) -> None:
        self.hal.set_leds("full")
        self.hal.play_sound("alarm_urgent", 1.0)  # lows: full volume, always (invariant 3)

    def _quiet_outputs(self) -> None:
        self.hal.stop_sound()
        self.hal.set_leds("ambient")

    def _chirp(self, respect_quiet_hours: bool) -> None:
        if respect_quiet_hours and self._quiet_hours(clock.now()):
            return
        self.hal.play_sound("chirp", self.settings.volume)

    # --- inputs ---

    def _start_full(self, reading: Reading | None) -> None:
        """A fresh, unacknowledged full alarm (never inherits a lower tier's ack)."""
        self._recovered = 0
        self._escalated = False
        self._deadline = clock.now() + timedelta(minutes=ESCALATION_MIN)
        self._go("active", "actual_low", reading)
        self._full_outputs()

    def _close_episode(self, reading: Reading | None) -> None:
        self._warning_acked = False
        self._low_forecasts = 0
        self._high_forecasts = 0
        self._recovered = 0
        self._deadline = None
        self._escalated = False
        self._go("idle", None, reading)
        self._quiet_outputs()

    def process_reading(self, reading: Reading) -> None:
        self.last_reading = reading
        s = self.settings
        if not reading.is_stale:
            self._last_fresh = reading
        if reading.is_stale:
            # Stale never changes a running low (it keeps sounding; the reading's is_stale
            # is the banner). With nothing running, it is a one-shot indicator tier.
            if not self._low_running() and self.trigger != "stale":
                self._go("active", "stale", reading)
                self._chirp(respect_quiet_hours=True)  # only lows override quiet hours
            return

        if self.trigger == "stale":  # a fresh reading clears the stale indicator
            self._go("idle", None, reading)

        below = reading.glucose_mgdl < s.low_threshold
        if below:
            self._recovered = 0
            if not (self._full_running() and self.trigger == "actual_low"):
                # From idle, pending, high, stale, or a timed-out warning (active, acknowledged,
                # or rearmed with trigger predicted_low): a fresh actual_low, never inheriting an ack.
                self._start_full(reading)
            return

        if self._full_running() and self.trigger == "predicted_low":
            # a timed-out warning (its condition is the FORECAST): readings above the low
            # threshold never close it; only recovered forecasts do (process_forecast)
            return
        if self._full_running():
            self._recovered += 1
            if self._recovered >= RECOVERY_READINGS:
                self._close_episode(reading)
            return

        above_high = reading.glucose_mgdl > s.high_threshold
        if above_high and self.state.state == "idle":
            self._go("active", "high", reading)
            self._chirp(respect_quiet_hours=True)
            if s.high_alert_mode == "remind" and s.high_remind_hours:
                self._high_remind_at = clock.now() + timedelta(hours=s.high_remind_hours)
        elif not above_high and self.trigger == "high":
            self._high_remind_at = None
            self._go("idle", None, reading)

    def process_forecast(self, forecast: Forecast) -> None:
        if self._full_running():
            if self.trigger == "predicted_low":  # a timed-out warning closes only on recovered forecasts
                if forecast.predicted_mgdl >= self.predicted_low_threshold():
                    self._high_forecasts += 1
                    self._low_forecasts = 0
                    if self._high_forecasts >= RECOVERY_FORECASTS:
                        self._close_episode(self.last_reading)
                else:
                    self._high_forecasts = 0
            return
        if not self.settings.predictive_enabled:
            return
        if forecast.predicted_mgdl < self.predicted_low_threshold():
            self._low_forecasts += 1
            self._high_forecasts = 0
            if self.state.state != "pending" and not self._warning_acked \
                    and self._low_forecasts >= self.settings.consecutive_predictions_n:
                self._deadline = clock.now() + timedelta(minutes=PENDING_TIMEOUT_MIN)
                self._go("pending", "predicted_low", self.last_reading)  # replaces high or stale
                self._warning_outputs()
        else:
            self._high_forecasts += 1
            self._low_forecasts = 0
            if self._high_forecasts >= RECOVERY_FORECASTS:
                if self.state.state == "pending":
                    self._close_episode(self.last_reading)
                elif self._warning_acked:
                    self._warning_acked = False  # the acked episode is over; a new crossing warns again

    def process_no_forecast(self) -> None:
        """A missing forecast (stale or gapped hour) resets the consecutive counts;
        a warning already on STAYS ON (only 2 real forecasts back above clear it,
        or an actual low takes over). George/Chris decision, 2026-09-26."""
        self._low_forecasts = 0
        self._high_forecasts = 0

    def acknowledge(self, source: AckSource) -> bool:
        """Returns True when the ack changed anything. Highs and stale need no ack."""
        st = self.state.state
        if st == "pending":
            self._warning_acked = True  # final for the episode; the actual low still fires
            self._deadline = None
            self._go("idle", None, self.last_reading, ack_source=source)
            self._quiet_outputs()
            return True
        if st in ("active", "rearmed") and self.trigger in LOW_TIERS:
            self._deadline = clock.now() + timedelta(minutes=REARM_MIN)
            self._escalated = False
            self._go("acknowledged", self.trigger, self.last_reading, ack_source=source)
            self._quiet_outputs()
            return True
        return False

    def tick(self) -> None:
        """Advance every deadline. Called every 30 s of clock time and by tests."""
        now = clock.now()
        st = self.state.state
        if st == "pending" and self._deadline and now >= self._deadline:
            # 5 min unacknowledged: the warning escalates to the full alarm; the strobe
            # step follows 5 min later like any other unacknowledged full alarm
            self._escalated = False
            self._deadline = now + timedelta(minutes=ESCALATION_MIN)
            self._go("active", "predicted_low", self.last_reading, escalated=True)
            self._full_outputs()
        elif st in ("active", "rearmed") and self.trigger in LOW_TIERS \
                and not self._escalated and self._deadline and now >= self._deadline:
            self._escalated = True
            self._go(st, self.trigger, self.last_reading, escalated=True)
            self.hal.set_leds("strobe")
            self.hal.play_sound("alarm_urgent", 1.0)
        elif st == "acknowledged" and self._deadline and now >= self._deadline:
            # clock time decides, not a new reading: a stale last reading falls back to the
            # last fresh one, so an acked low that lost its sensor still re-arms
            r = self._last_fresh
            still_below = r is not None and r.glucose_mgdl < self.settings.low_threshold
            if still_below:
                self._escalated = False
                self._deadline = now + timedelta(minutes=ESCALATION_MIN)
                self._go("rearmed", self.trigger, self.last_reading)
                self._full_outputs()
        if self.trigger == "high" and self._high_remind_at and now >= self._high_remind_at:
            self._chirp(respect_quiet_hours=True)
            self._high_remind_at = now + timedelta(hours=self.settings.high_remind_hours or 1)
