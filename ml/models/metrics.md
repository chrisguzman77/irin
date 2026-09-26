# Irin forecaster: metrics (the judging Q&A sheet)

<!-- HEADLINE: the public mirror keeps only this block. -->
## Headline

Pending step 3b (model vs baseline B on events at the chosen threshold).

<!-- END HEADLINE -->

## Event definitions (ml/events.py; every number below uses these)

The replay runs every reading through the backend's warning rule
(backend/app/alarm.py, Idle -> Pending), not a proxy for it.

- **Forecast:** the predicted glucose 30 min ahead, made from the last 60 min
  as 13 five-minute slots (`features.slot_windows`, the same code the Pi runs
  via `latest_window`). A dropped reading leaves its slot empty (NaN, never
  interpolated; XGBoost treats it as missing). A window is usable when the
  current reading is present, no more than 5 slots in a row are empty, and no
  gap (over 30 min without a reading) falls inside it; a stretch's first hour
  after a gap has no forecast. No usable window means no forecast, on the
  device and here.
- **Warning:** starts on 2 consecutive forecasts below the threshold; clears
  on 2 consecutive forecasts at or above it; a reading with no forecast
  breaks both counts and leaves a warning that is on unchanged. No warning
  starts during an actual low (actual low outranks predicted low).
- **Low event:** any single reading under 70 mg/dL. A new event needs 2
  consecutive readings back at or above 70. Single-reading dips (possible
  sensor artifacts such as compression lows) are kept in the denominator;
  the held-out review (step 3.6) is where artifacts are flagged.
- **Lead:** crossing time minus the start of the warning that is on at the
  crossing.
  - **detected:** 10 <= lead <= 60 min.
  - **late:** lead under 10 min (warned, too late to act on).
  - **long:** lead over 60 min. The warning was on, but one that has sat on
    for over an hour is not credited as a prediction; these are excluded
    from detection and from the lead statistics.
  - **missed:** no warning on at the crossing.
- **Forecastable:** at least 2 forecasts exist between 40 and 10 min before
  the crossing (both ends inclusive), the fewest any forecaster needs to fire
  the 2-consecutive rule with the minimum 10-min lead. Lows after a gap or in
  a sensor's first hour are not forecastable by anything. Detection is
  reported both over all lows and over forecastable lows only.
- **False alarm:** a warning that clears with no crossing. Split by the lowest
  actual reading between its start and its clear: **near** (under 80; glucose
  did come close) and **far** (80 and up; glucose never came near).
- **Per night / per day:** false alarms whose warning started in the night
  window (22:00-07:00), divided by sensor-nights = night-window readings / 108;
  all-day false alarms divided by sensor-days = readings / 288.

## Core section

Pending step 3b and 3.7: split dates, the three event numbers, the baseline
comparison, the chosen threshold and its curve position, the feature list,
excluded events.
