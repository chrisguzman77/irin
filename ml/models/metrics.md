# Irin forecaster: metrics (the judging Q&A sheet)

<!-- HEADLINE: the public mirror keeps only this block. -->
## Headline

Irin warns of an overnight low **before** it happens: over 5 out-of-sample
8-week blocks of one person's real CGM history (24 overnight lows, about
276 sensor-nights), the forecaster warned **10-60 min ahead of 75% of
overnight lows (18 of 24)**, with a **median lead of 27.5 min**, at **0.20
false alarms per night** (about one every 5 nights).

A plain 15-min trend line, at its best setting, catches about the same
share (79%, 19 of 24) but raises **0.47 false alarms per night**, more than
twice as many. The model's gain is fewer wrong alarms at the same
detection; a device that cries wolf every other night gets unplugged.

Scored by replaying every reading through the device's own warning rule.
Every forecast in these numbers comes from a model trained only on data
from before it. n = 1 person, retrospective.

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

### Data and split

- **Source:** 8 Dexcom Clarity exports from one person, cleaned by
  ml/clean_clarity.py (dedupe first, Low -> 39, High -> 401, spike filter
  only, never interpolated): 169,188 readings, 2024-12-05 to 2026-09-20.
  Two multi-week stretches from a different CGM brand are gaps, not data.
- **Dataset (ml/build_dataset.py):** 167,277 rows (98.9% of readings), one
  per reading with a usable 60-min window and a reading exactly 30 min
  later in the same gap-free stretch; 0 rows cross a gap; every input is at
  or before t. No forecast in a stretch's first hour (0.53% of readings).
- **Split by time, never at random:** train up to 2026-07-26 13:38, held
  out 2026-07-26 13:38 to 2026-09-20 (the final 56 days; 151,970 / 15,307
  rows). Training rows stop 30 min before the split so no label crosses it;
  early stopping uses the last 28 days of the TRAIN period only.
- **Rolling folds:** 5 consecutive 56-day blocks, 2025-12-14 to 2026-09-20
  (the last one is the held-out period). Each block is forecast by a model
  trained only on rows whose label lands before the block starts, so all
  pooled numbers are out-of-sample (274 sensor-days, 276 sensor-nights,
  24 overnight / 53 total lows).

### Model (ml/train.py -> ml/models/forecast_v1.json)

- XGBoost 3.4.1, `reg:quantileerror` at quantile 0.2, on the 30-min DELTA
  (the current value is added back): eta 0.05, max_depth 6,
  min_child_weight 10, subsample 0.8, colsample 0.8, hist, 657 rounds.
  Saved as XGBoost JSON (4.1 MB), never a pickle.
- **16 features, glucose only** (ml/models/features.py, the one function
  training and the Pi share): current value; lags at 5/10/15/30/60 min;
  rate of change over 5/15/30 min; acceleration; rolling mean/std/min of
  the window; sin/cos of hour; night-window flag. No insulin or carb
  inputs, so the forecaster runs on any CGM feed. A dropped reading is a
  missing value, never filled in.
- **Pi check:** forecast_v1_check.json holds a SYNTHETIC window and the value
  `predict()` must return for it (75.873); the Pi reproduces it within 1e-3.

### Chosen operating point (checkpoints 3 and 4, George and Chris, 2026-09-26)

- **Model:** a **20th-percentile (quantile 0.2) forecast** of glucose at
  t+30: "how low could this plausibly go", not the most likely value. A
  squared-error model had the best average error but forecast the mean,
  learned that lows get treated and rebound, and detected 0-4% of lows; it
  was rejected on events.
- **Warning threshold: 85 mg/dL on that quantile forecast**, 2 consecutive
  forecasts. It is not a threshold on an ordinary glucose forecast, and the
  actual-low alarm stays at 70.
- **Why 85:** it is the peak of the sweep (70-90) for every quantile tried
  (0.1 / 0.2 / 0.3); above it, warnings start more than 60 min early and
  stop counting as detections. Quantile 0.3 at 85 was the runner-up: 71%,
  20-min lead, 0.11 false alarms per night.

### Results

Overnight (22:00-07:00), the bedside use case:

| Scope | Forecaster | Thr | Detected 10-60 min | Median lead | False / night | Far / night |
|---|---|---|---|---|---|---|
| Rolling folds (24 lows) | **Model q0.2** | **85** | **75% (18)** | **27.5 min** | **0.20** | **0.14** |
| Rolling folds | B 15-min trend | 80 | 79% (19) | 25 min | 0.47 | 0.42 |
| Rolling folds | B 15-min trend | 70 | 58% (14) | 25 min | 0.35 | 0.30 |
| Rolling folds | C weighted rate | 80 | 79% (19) | 25 min | 0.48 | 0.42 |
| Held-out 8 wk (5 lows) | **Model q0.2** | **85** | **100% (5)** | 25 min | 0.24 | 0.16 |
| Held-out 8 wk | B 15-min trend | 80 | 100% (5) | 25 min | 0.35 | 0.27 |
| Full history (65 lows) | B 15-min trend | 80 | 60% (39) | 30 min | 0.51 | 0.44 |

- Persistence (forecast = the current value) never warns ahead of a crossing
  at 70 and catches at most 29% at 85 (full history, overnight): forecasting
  is doing the work.
- Full-history rows are for the untrained baselines only; the model is never
  scored on data it trained on.
- **Point error** on held-out rows (mg/dL at t+30, MAE overall / where the
  truth is under 100): persistence 18.96 / 19.07, B 21.12 / 20.64, model
  20.34 / 9.61. The model is a low quantile, so it is biased low by design
  overall and most accurate exactly where lows happen.

### Excluded events (step 3.6)

All 6 held-out lows were plotted for review by the sensor wearer
(ml/review_lows.py). **None were excluded: all 6 are counted**, including 2
with the fast-recovery shape typical of sensor artifacts (a rise of more
than 4 mg/dL/min after the lowest reading). The model warned 10-60 min ahead
of all 6, so an exclusion could not have raised its detection. Training data
is never filtered by this review.

### Limitations (say these before anyone asks)

- **n = 1, retrospective.** One person's history, replayed; not a trial.
- **24 overnight lows** in the rolling folds: one low is about 4 percentage
  points, so the detection gap to B (18 vs 19 of 24) is within noise. The
  false-alarm gap (55 vs 131 warnings) is not.
- **Daytime is weaker.** Over the whole day (53 lows) the model catches 64%
  at 0.50 false alarms per day, against B's 74% at 1.16 per day. The
  operating point was chosen for the bedside night; daytime lows are
  mostly caught by the person, awake.
- **The history includes treatment.** Lows in the record were often treated,
  so what came after them is not what would have happened untreated. This
  is why a model forecasting the most likely value failed, and why the
  shipped model forecasts a low percentile instead.
- **Every single reading under 70 counts as a low**, including possible
  sensor artifacts, and lows right after a sensor gap count against the
  forecaster even when no forecaster could have seen them (reported as
  "forecastable" in the tables printed by ml/evaluate.py).
- The warning rule is replayed from alarm.py's written spec, confirmed by
  Chris: a missing forecast resets the consecutive count, and a warning
  that is on stays on until 2 forecasts at or above the threshold, or an
  actual low takes over.

Reproduce: `python -m ml.clean_clarity && python -m ml.build_dataset &&
python -m ml.train && python -m ml.evaluate` (the raw exports stay off-repo).
