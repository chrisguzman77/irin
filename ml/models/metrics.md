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
from before it; the model's settings were chosen on these same blocks, so
treat 75% as an upper estimate. n = 1 person, retrospective.

**Rounds (Clinical Signal Cards):** on one confirmed basal increase in the
same history, Rounds' Basal Check would have flagged rising overnight
glucose **at least 2 weeks before the change, on 69% of mornings**, against
a 14% background rate in stable stretches. Retrospective, observational,
n = 1: "since the change", never "the change caused".

<!-- END HEADLINE -->

## Event definitions (ml/events.py; every number below uses these)

The replay runs every reading through the WARNING half of the backend's
alarm rule (alarm.py's written spec, Idle -> Pending, confirmed by Chris). It
models when a warning starts and clears, which is what detection, lead, and
false alarms depend on; it does not model what happens after (escalation of
an unacknowledged warning after 5 min, acknowledgment, the actual-low alarm's
own states).

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
  rows). Training rows stop 35 min before the split (labels land 27.5-32.3
  min after t, from slot rounding) so no label crosses it;
  early stopping uses the last 28 days of the TRAIN period only.
- **Rolling folds:** 5 consecutive 56-day blocks, 2025-12-14 to 2026-09-20
  (the last one is the held-out period). Each block is forecast by a model
  trained only on rows whose label lands before the block starts, so all
  pooled numbers are out-of-sample (274 sensor-days, 276 sensor-nights,
  24 overnight / 53 total lows).

### Model (ml/train.py -> ml/models/forecast_v1.json)

- XGBoost 3.4.1, `reg:quantileerror` at quantile 0.2, on the 30-min DELTA
  (the current value is added back): eta 0.05, max_depth 6,
  min_child_weight 10, subsample 0.8, colsample 0.8, hist, 559 rounds.
  Saved as XGBoost JSON (3.5 MB), never a pickle; predict.py loads it at
  import, so a missing or broken model fails at boot.
- **16 features, glucose only** (ml/models/features.py, the one function
  training and the Pi share): current value; lags at 5/10/15/30/60 min;
  rate of change over 5/15/30 min; acceleration; rolling mean/std/min of
  the window; sin/cos of hour; night-window flag. No insulin or carb
  inputs, so the forecaster runs on any CGM feed. A dropped reading is a
  missing value, never filled in.
- **Pi check:** forecast_v1_check.json holds a SYNTHETIC window and the value
  `predict()` must return for it (75.953); the Pi reproduces it within 1e-3
  (ml/tests/test_train.py checks the same on the laptop). The Pi passes
  `features.latest_window` at least the last 100 min of readings
  (HISTORY_MIN); with that, it builds exactly the training window (tested).

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
| Held-out 8 wk (5 lows) | **Model q0.2** | **85** | **100% (5)** | 25 min | 0.22 | 0.15 |
| Held-out 8 wk | B 15-min trend | 80 | 100% (5) | 25 min | 0.35 | 0.27 |
| Full history (65 lows) | B 15-min trend | 80 | 60% (39) | 30 min | 0.51 | 0.44 |

- Persistence (forecast = the current value) never warns ahead of a crossing
  at 70 and catches at most 29% at 85 (full history, overnight): forecasting
  is doing the work.
- Full-history rows are for the untrained baselines only; the model is never
  scored on data it trained on.
- **Point error** on held-out rows (mg/dL at t+30, MAE overall / where the
  truth is under 100): persistence 18.96 / 19.07, B 21.12 / 20.64, model
  20.36 / 9.64. The model is a low quantile, so it is biased low by design
  overall and most accurate exactly where lows happen.

### Excluded events (step 3.6)

All 6 held-out lows were plotted for review by the sensor wearer
(ml/review_lows.py). Chris did not flag any before this sheet was written,
so **none were excluded: all 6 are counted**, including 2
with the fast-recovery shape typical of sensor artifacts (a rise of more
than 4 mg/dL/min after the lowest reading). The model warned 10-60 min ahead
of all 6, so an exclusion could not have raised its detection. Any later
flag in ml/data/review_flags.csv drops that low from the held-out AND the
rolling-fold counts on the next `python -m ml.evaluate`. Training data is
never filtered by this review.

### Limitations (say these before anyone asks)

- **n = 1, retrospective.** One person's history, replayed; not a trial.
- **24 overnight lows** in the rolling folds: one low is about 4 percentage
  points, so the detection gap to B (18 vs 19 of 24) is within noise. The
  false-alarm gap (54 vs 131 warnings) is not.
- **The settings were chosen on the blocks they are scored on.** The quantile
  objective, quantile 0.2, and threshold 85 were picked after seeing the
  rolling-fold and held-out results (85 also lies above the planned 70/75/80
  sweep). The model itself never trained on a block it is scored on, but the
  choice of settings did see them, so 75% is optimistic; a fresh period of
  data is the honest test. The false-alarm advantage over B holds at every
  nearby setting (0.13-0.29 per night against B's 0.35-0.56), so the
  conclusion does not hinge on the pick.
- **Daytime is weaker.** Over the whole day (53 lows) the model catches 62%
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
  actual low takes over. Only the warning half is replayed (see Event
  definitions); alarm.py itself is not written yet.
- A reading is dropped (training and replay) when the next one arrives
  within 2.5 min; the Pi has already forecast from it by then. Rare
  (26 of 169,188 readings) and tiny.

Reproduce: `python -m ml.clean_clarity && python -m ml.build_dataset &&
python -m ml.train && python -m ml.evaluate` (the raw exports stay off-repo).

## Rounds section (george.md steps 5-6; step 7 adds the demo windows)

### What is real, what is inferred, what is not in the data

- **Real:** the glucose, every night of it (ml/label_history.py runs the
  cleaned history through ml/models/nights.py, the one module the device's
  ledger and cards also use).
- **Inferred from glucose, and labeled so everywhere:** reason codes (no
  treatments on history: late meal from a fast early-night rise, treated
  low from a rebound of more than 60 mg/dL within 2 h; basal timing,
  exercise, and away are never asserted), and the alarm events, which are
  forecast_v1's warnings replayed through the warning rule at 85. A night
  with nothing visible in the glucose is coded clean with code source
  "inferred".
- **Not in the data, never fabricated:** acknowledge times, escalation,
  presence, morning recall answers, stomach check-ins, injection logs. They
  appear only as a labeled overlay in the demo scenarios (step 7), and the
  Basal Check demo is described every time as: "glucose real; reason codes
  inferred and labeled; acknowledge, presence, and recall data are a
  labeled overlay".
- **Therapy changes:** 2 basal increases confirmed by Chris (A and B) and 1
  date picked from the data and NOT confirmed (C, labeled inferred and never
  used in a headline). Candidates were proposed by ml/find_basal_changes.py
  from a drop in overnight LEVEL, a different signal from the overnight RISE
  the Basal Check tests, so the search did not favor dates the rule catches.
  The dates themselves stay in the gitignored ml/data/therapy_changes.txt.

### The three measured numbers (ml/evaluate_rounds.py)

Rule thresholds are the starting values from docs/plans/chris.md R8 / R10,
applied by the validation script until backend/app/rounds/standing.py and
step_watch.py land (then rerun with theirs).

**1. Basal Check lead time** (the rule run every morning over the prior
history; lead = change date minus the first morning it flags rising nights):

| Change | Source | Lead | Mornings flagged | Last 14 mornings flagged |
|---|---|---|---|---|
| A | confirmed | at least 15 days (the data starts then) | 69% | 64% |
| B | confirmed | 60 days, but not in the final 2 weeks; about 5 weeks of it is a CGM gap | 54% | 0% |
| C | inferred | none: sporadic flags at the background rate, none in the run-up | 34% | 0% |

Only A supports a claim. The background rate below is what makes 69% mean
something.

**2. Firing rates over stable stretches** (windows more than 30 days from
every listed change; 28 fortnights, 76 evaluable 5-day windows):

| Signal | Fires | Note |
|---|---|---|
| Basal Check, rising (> +30, >= 70%) | 14% | |
| Basal Check, falling (< -30, >= 70%) | 21% | |
| Basal Check, "possibly too high" (>= 5 near-misses in 14 nights) | 14% (out-of-sample 0 of 3) | moved from 3 at checkpoint 6 |
| Hypo Response, glucose-side proxy (>= 1 inferred-unfelt low) | 0% | escalations, re-arms, ack times, and reported unfelt lows are not in the data |
| Step Watch, low-point shift <= -15 | 42% | kept; see below |
| Step Watch, TBR > 4.0% | 1% | |
| Step Watch, near-misses >= 2 in 5 days | 21% (out-of-sample 27%) | |
| Step Watch, highs (>= 2 ketone-risk episodes) | 100% | see below |

Tolerance (stomach check-ins) and awareness (morning answers) cannot be
tuned on history; no number is invented for them.

**3. Detection lag** (days after a change until the 5-night low point
differs from the 14 nights before by 15 mg/dL or more): 5 days for A, B, and
C (shifts of -35, -24, and -42 mg/dL), the earliest the rule can answer.
Because the same rule fires in 42% of stable windows for this patient, the
lag is not evidence of sensitivity on its own.

### Threshold decisions (checkpoint 6, George and Chris, 2026-09-26)

- **Near-miss count for Basal Check's "possibly too high": 3 -> 5 per 14
  nights.** The one change, made for a mechanical reason rather than to fit
  this patient: the shipped warning (85 on a 20th-percentile forecast)
  produces about 0.2 false warnings per night by design, so about 2.8 per 14
  nights, and ">= 3" fired in half of all stable fortnights. At 5 it fires
  at the same background as the rising Basal Check (14%). The sweep: 3 -> 50%,
  4 -> 25%, 5 -> 14%, 6 -> 0%.
- **Low-point shift -15: kept.** It fires in 42% of stable windows for this
  patient (-20: 34%, -25: 26%, -30: 19.5%), but it is a safety signal during
  a titration (missing a real shift costs more than a false one), it is
  rate-limited to one check per step, and the Step Watch worked example
  (-22) must fire.
- **Step Watch near-misses >= 2: kept** (the worked example has exactly 2).
- **Ketone-risk definition (>= 200 for >= 120 min): kept, and documented.**
  It fires in every window for this patient (>= 250 for 240 min: 71%; >= 300
  for 240 min: 3%): his highs are real, but for someone who runs high most
  nights an absolute definition carries no news. The better design is
  relative to the patient's own baseline; that is a Step Watch design
  change for later, not a threshold tweak. It does not affect the demo,
  whose Step Watch scenario is SYNTHETIC.
- **Basal Check (5 clean nights, beyond +/-30, 70% same direction): kept.**

### Leakage guard (george.md step 6.4)

Near-miss counts rest on forecast_v1's replayed warnings. Before the
forecaster's train/test split the model trained on those nights, so a
near-miss there is not evidence; every near-miss signal above is also
reported on out-of-sample windows only (few: 3 fortnights, 11 five-day
windows).

The guard's answer for every demo scenario (demo/scenarios/, step 7), i.e.
whether any warning or near-miss the scenario shows could come from a model
that trained on that same night:

| Scenario | Glucose | Warnings / near-misses come from | Evidence? |
|---|---|---|---|
| the_save | real, date-shifted | forecast_v1 on a HELD-OUT night (after the split) | yes: out-of-sample |
| basal_change_1 | real, date-shifted | forecast_v1 RETRAINED WITHOUT the scenario's window (plus 35 / 65 min margins), replayed at 85 | yes: out-of-sample |
| rearm_low | real, date-shifted | forecast_v1, IN-SAMPLE (the night is before the split) | no: it demonstrates the re-arm, not forecast accuracy, and says so in demo/scenarios/README.md |
| normal_night, failure, meal_context, high_spike | real, date-shifted | forecast_v1, in-sample | nothing claimed: they demonstrate display, staleness, context, and the high alert, not a warning |
| titration_synthetic | SYNTHETIC (2020 timestamps) | a labeled overlay written to reproduce the R10 worked example | no model involved |

### Demo scenarios: what is real, what is not (step 7)

- **Real glucose, date-shifted by whole days** (the time of day the forecaster
  uses is unchanged; the real dates and offsets never enter the repo): the six
  core scenarios and basal_change_1. Chris vouches for the real nights at
  checkpoint 7.
- **Inferred and labeled:** every reason code in a companion JSON
  (code_source "inferred"), and the glucose side of basal_change_1's alarm
  events.
- **SYNTHETIC overlay, labeled** (overlay_synthetic: true): acknowledge
  times, ack source, escalation, presence, and recall answers in
  basal_change_1 (its one nocturnal low is left unanswered, never "fine").
- **SYNTHETIC throughout:** titration_synthetic, built so nights.py yields the
  Step Watch amber worked example exactly (coverage 1,390 / 1,440; low point
  98 -> 76; TBR 58 / 1,440; 2 near-misses; 1 low not remembered; rough 3 of 5).
- **The dose units** in basal_change_1 (18) were given by George; until Chris
  confirms they are his real dose, say "units illustrative".
- The Basal Check demo is described every time, verbatim: "glucose real;
  reason codes inferred and labeled; acknowledge, presence, and recall data
  are a labeled overlay". Never an unqualified "built from real nights".

### Caveats, in the spec's own words

n = 1; observational; retrospective. "Since the change", never "the change
caused". All Rounds thresholds are starting values chosen at design time and
tuned here against one person's data, never presented as researched facts.

