# George — ML plan (docs/plans/george.md)

Read this at the start of any session working ml/. CLAUDE.md has the
repo rules; this file is the full method. Follow it in order; each step
names its check. Baselines are evaluated BEFORE the model exists. Steps
1-3 are core Irin (tier 1). Steps 4-8 are the Rounds work (the updated
Rounds spec, docs/Irin_Rounds.pdf, 2026-09-25: night classification,
the metrics module, the three measured numbers, the demo windows) and
start only after the core forecaster ships and The Save plays end to end.

## Lane

Owns ml/: data cleaning, dataset, training, evaluation, the model
artifact, and the Rounds classification, metrics, and labeling work. Two
boundary files that Chris's backend calls: ml/models/predict.py (the
forecaster) and ml/models/nights.py (the ONE night-classification and
metrics module), plus cloud/sql/ (the Tiger Cloud hypertables and
continuous aggregates the My Irin dashboards read; step 9).
Change either signature only with contracts-level care and a journal
Interface-changes note. Demo scenarios live in demo/ (shared, Chris owner
of record): you draft them on a branch, Chris merges.

## How this lane works: Claude drives, George steers

The whole pipeline (clean, build, train, evaluate) runs in about two
minutes total, so Claude writes AND runs every script inside its own
loop, reads the output, and iterates. George's job is the judgment at
the checkpoints below, not relaying runs. At each checkpoint Claude
STOPS, shows the result, and waits for George's approval before
starting the next step (CLAUDE.md's plan-first rule, applied per step).

The one exception is anything slower than a minute (a hyperparameter
search, or a loop-based dataset build): George runs it in a plain
terminal and pastes the printed result back. Keep any search small.

Checkpoints, in order:
1. After cleaning: George confirms the reading count (~169k), the gap
   list (sensor changes plus the two Libre stretches), and the spike
   filter's drop count look sane.
2. After the dataset build: George confirms NO feature uses future
   information. The most important review in the pipeline: a leak
   makes the model look brilliant in eval and useless on the device.
3. After baselines + first model: George judges whether the model
   beats the trend baseline on EVENTS (detection, lead time, false
   alarms), not just on average error.
4. Threshold choice: George and Chris pick the operating point from
   the sweep plot. A product decision, not a calculation.
5. Held-out low review: Chris marks sensor artifacts (only the person
   who wore the sensor can).
6. Rounds thresholds (step 6): George and Chris read the lead-time,
   false-alarm, and detection-lag table over stable stretches and keep
   or move the starting values.
7. Demo windows (step 7): Chris vouches for the basal-change dates and
   the real nights around them, and answers the recall question for any
   low he remembers; everything else in the overlay is labeled
   synthetic, and reason codes on history are always marked inferred.

## Data privacy inside Claude's context

Running a script keeps the data on George's laptop; only what the
script PRINTS enters the conversation. So: scripts print SUMMARIES
only (counts, gap lists, metrics, errors). Claude never opens, reads,
or prints raw CSV rows (no viewing the file, no head/cat, no "print
the first 5 rows"). The raw files carry Chris's name, date of birth,
and full glucose history. To show Claude the file layout, George
pastes the column names plus two or three made-up rows. The same rule
covers ml/data/therapy_changes.txt (Chris's therapy-change dates) and
the labeled-nights file: summaries only.

## The data (from Chris, off-repo, NEVER committed)

8 Dexcom Clarity CSVs, 2024-12-05 to 2026-09-20 (about 22 months),
169,202 EGV readings after dedup, ~132 low events (<70), 58 overnight.
Files overlap (2/3 share two months, 4/5 share a day). Two multi-week
holes (mid-Feb to late-Mar 2025, two weeks mid-Jun 2025) are a different
CGM brand: treat as gaps, do not import that data. Raw files live only
in gitignored ml/data/ (they contain name and DOB). Committable outputs:
forecast_v1.json, metrics.md, and date-shifted scenarios in demo/ only.

## Step 1: clean_clarity.py

1. Parse only Event Type == EGV; discard metadata header rows.
2. Concatenate all files, dedupe on timestamp FIRST (overlaps otherwise
   double-weight those months).
3. Map literal "Low" → 39 and "High" → 401 (sensor range caps; the
   "Low" rows are the deepest lows, keep them).
4. Spike filter, the ONLY value-based cleaning: drop x(t) when
   |x(t)-x(t-1)| > 30 AND |x(t+1)-x(t)| > 30 AND opposite signs.
   Real glucose tops out near 4 mg/dL/min (20 per 5-min reading); one
   30+ move is suspicious, two opposite ones in 10 min is electronics.
   Opposite-signs protects real crashes (second move continues down →
   never fires). The dropped reading becomes a HOLE; NEVER interpolate.
5. Do NOT auto-delete suspicious lows (compression lows). Not reliably
   separable from real fast lows, low events are the scarcest resource,
   and the live sensor produces them too. The label is what the sensor
   will say; that is also what the deployed device alarms on.
6. Gap = any interval > 30 min.
   Check: cleaned count within a few hundred of 169k; printed gap list
   matches the known pattern (sensor changes + the two brand holes).

## Step 2: dataset builder

1. One row per reading, only when the full 60-min feature history AND
   the t+30 label sit inside one gap-free stretch.
2. Label = glucose(t+30) − glucose(t), the DELTA. Add current value back
   at inference for display. Absolutes let a model score well by echoing
   the current number; the delta forces it to learn dynamics.
3. Features (~15): current value; lags at 5/10/15/30/60 min; rate of
   change over 5/15/30 min; acceleration (change in 5-min rate between
   now and 15 min ago); rolling mean/std/min of the window; sin/cos of
   hour + night-window flag. No insulin/carb features in v1 (the export
   has almost none logged; context is backend rules on top). Raw mg/dL,
   no normalization for trees. Glucose-only features are also why Irin
   Brain runs on any CGM feed without the device; keep it that way.
   Check: row count in the low 100k; no window crosses a printed gap;
   no NaNs.

## Step 3: evaluation, then training

1. Split by TIME, never randomly: hold out the final 8 weeks (~11 low
   events). Random splits leak via autocorrelation and lie.
2. Baselines FIRST: (A) persistence, (B) linear extrapolation of the
   last 15-min trend. If XGBoost does not clearly beat B at the event
   level, fix features before hyperparameters. If the model never beats
   B, the baseline ships (spec rule).
3. Point metrics (sanity): MAE overall, and MAE where true < 100.
4. Event metrics (the real eval): replay held-out weeks through the
   actual alarm rule (forecast crosses threshold, N = 2 consecutive):
   detection rate (warning ≥ 10 min before crossing 70), median lead
   time, false alarms per night. Write this event replay as a reusable
   function (ml/events.py): step 5 runs the same thing over the whole
   history.
5. Threshold sweep (predicted 70/75/80): plot detection vs false alarms
   per night; choose the point keeping false alarms under ~1 per two
   nights (Chris goes low 1.4x/week; a nightly false alarm gets the
   device unplugged, and then detection is zero).
6. Human review of EVAL events only: one plot per held-out low; Chris
   flags obvious artifacts (steep midnight V, instant full recovery, no
   treatment). Flagged events leave the denominator, noted in
   metrics.md. Training data stays untouched.
7. ml/models/metrics.md records: split dates, the three event numbers,
   baseline comparison, chosen threshold + curve position, feature
   list, excluded events. That file is the judging Q&A sheet.

## Step 4: nights.py, the shared classification and metrics module (Rounds)

The Rounds equivalent of features.py: every reason code and every
per-night and per-window metric is computed in ONE place,
ml/models/nights.py, imported by Chris's ledger, Standing Cards, and
Step Watch (backend/app/rounds/) AND by your validation scripts. Never
implement a metric in two places; a definition that drifts between
validation and serving fails silently. Pure functions, numpy only (no
pandas, so the Pi's backend needs nothing new), no I/O. Source: the
updated Rounds spec (docs/Irin_Rounds.pdf, 2026-09-25), "Signals and
Card Rules".

1. `classify_night(readings, treatments, alarm_events, presence,
   night_window)` → (reason_codes, code_source). Codes, one or more per
   night: late_meal (carbs within 3 h before night start, or during the
   night), late_correction (bolus within 3 h before night start, so IOB
   above zero at night start), basal_late / basal_missed (basal logged
   more than 60 min after the usual time, or not logged), exercise
   (logged after 17:00), treated_low (any low alarm followed by a rise
   above about 60 mg/dL within 2 h), stale (coverage under 85% of
   expected), away (the presence toggle set to Away), clean (none of the
   above). code_source is "logged" when the patient logs; on history and
   on a Brain-only patient late_meal is inferred (a rise faster than
   about 2 mg/dL/min in the first 2 h of the night), treated_low is
   inferred (a reading under 70 followed by a rise above 60 within 2 h),
   stale is fully available, and basal_late, exercise, and away are
   "unknown" ("context unavailable"), never assumed clean.
2. `night_metrics(readings, alarm_events, night_window)` → coverage_pct
   (non-stale readings ÷ expected, expected = window hours × 12; 9 h ×
   12 = 108, 0.85 × 108 = 91.8, so ≥ 92 readings is adequate),
   rise_mgdl (glucose at 07:00 minus glucose at night start + 2 h, each
   a median over a 15-minute window), dawn_rise_mgdl (03:00 to 07:00,
   same method), low_point_mgdl (minimum of the 15-minute rolling
   median), tbr_pct (under 70, full day and overnight separately),
   minutes_below_70 and auc_below_70 (nocturnal burden), near_miss_count
   (predicted-low warnings acknowledged or recovered with no actual
   crossing within 60 min), level2_count (readings under 54 confirmed by
   2 consecutive readings), ketone_risk_episodes (runs at or above 200
   lasting at least 120 min). Stale-flagged readings never count toward
   coverage.
3. `low_events(readings, treatments, alarm_events, night_window)` → one
   LowEvent per nocturnal low (under 70 confirmed by 2 consecutive
   readings; a new event needs 2 readings back at or above 70): nadir,
   minutes_below_70, auc, recovery_slope (mg/dL per min over the 30 min
   after the nadir), carbs_logged_within_30min, inferred_unfelt (run ≥
   20 min, slope < 1.0, no carbs within 30 min).
4. `standing_window(night_records, low_events, recalls)` over 14
   nights → clean_nights, rise_median_clean, same_direction_share,
   near_misses, escalated_warnings, rearms, median_ack_min (events with
   presence_during = "home" only; None otherwise, never zero),
   nocturnal_lows, answered, unfelt_lows (answers woke_no_symptoms +
   dont_remember), no_answer, unfelt_low_rate = unfelt ÷ answered
   (no_answer never in the denominator, reported beside it; 2 of 3
   answered = 0.667). Every value carries its confidence label
   (measured / reported / inferred) in a parallel dict, and a value that
   cannot be computed is None, never zero.
5. `step_window_metrics(window_records, baseline_records, symptom_checks,
   injections)` → low_point_shift (median window low point − median
   baseline low point), tbr_pct, near_misses, coverage_pct,
   baseline_nights, tolerance counts (fine / rough / cant_eat / missing),
   adherence (logged weekly injections against expected, plus a
   dose-mismatch flag), ketone_risk_episodes.
   Check: ml/tests/test_nights.py reproduces the spec's worked examples
   by hand: 8 clean of 14 with 6 rising → 6 ÷ 8 = 75%, median +42 → Basal
   Check fires; 1,390 ÷ 1,440 = 96.5%; 76 − 98 = −22; 58 ÷ 1,440 =
   4.03%; 0.85 × 108 = 91.8 → 92; 4 nocturnal lows, 3 answered, 2 unfelt →
   2 ÷ 3 = 67% with 1 no answer; a recovery slope of 0.99 is inferred
   unfelt and 1.0 is not; a snack 3 h 01 min before night start is not
   late_meal.

## Step 5: label the real history (ml/label_history.py)

1. Run the cleaned 22 months through classify_night, night_metrics, and
   low_events with the shipped forecaster and the actual alarm rule (the
   ml/events.py replay from step 3.4) to produce INFERRED reason codes
   and INFERRED alarm events per night: warning fired, crossed_actual,
   level 2, low point, rise, burden. These are the glucose-side fields
   of NightRecord, AlarmEvent, and LowEvent, and they are real.
2. What cannot be inferred, because the export has no logs: acknowledge
   times, escalation, presence, morning answers, basal timing, exercise.
   Never fabricate them into the labeled history; they enter only as a
   labeled overlay in demo scenarios (step 7), and the reason codes on
   history are always marked inferred, never presented as logged.
3. Output ml/data/nights_labeled.csv (gitignored) plus a printed
   summary: nights, clean nights (inferred), low nights, near-misses,
   level 2 nights, per-month counts. Every label is marked inferred in
   metrics.md.
   Check: the printed low-night count is consistent with step 1's ~132
   low events / 58 overnight.

## Step 6: the three measured numbers and threshold tuning

All Rounds thresholds are STARTING VALUES chosen at design time, tuned
here against real data, never presented as researched facts. Three
numbers make technical execution credible, all from Chris's real 22
months, never from the synthetic scenario:

1. **Retrospective lead time for Basal Check.** Chris lists his known
   basal increases (and any other therapy changes) with confidence
   notes in gitignored ml/data/therapy_changes.txt, choosing 2 to 3 away
   from the two Libre holes (mid-Feb to late-Mar 2025, two weeks
   mid-Jun 2025). For each, run the Basal Check rule daily over the
   prior history and record the first trigger date; lag = change date −
   first trigger. The claim becomes "Rounds would have flagged this N
   weeks earlier", retrospective, n of 1, observational.
2. **False-alarm rate.** Run the Standing Card rules and the Step Watch
   amber rules over stable-therapy stretches (the runs between listed
   changes, excluding gaps and any window within 30 days of a change)
   and report how often they fire. Scale: 22 months is about 22 × 30.4 =
   669 days, so roughly 669 ÷ 5 ≈ 133 non-overlapping 5-day windows and
   about 47 fortnights before removals. Per signal: Basal Check (both
   directions), Hypo Response's glucose-side signals, shift ≤ −15, TBR >
   4.0%, near-misses ≥ 2. Wake resistance, response latency, and
   tolerance cannot be tuned on history (no acks, no answers); say so in
   metrics.md instead of inventing a number.
3. **Detection lag.** Days from a real therapy change until the
   low-point-shift rule first flags a shift of 15 mg/dL or more in
   either direction.
4. **Leakage guard.** Any model-based number on a demo window (a
   near-miss, a warning) must come from the held-out 8 weeks or from a
   retrained copy of the forecaster that excludes that window, and
   metrics.md says which. A near-miss counted with a model that trained
   on the same night is not evidence.
5. Keep or move the starting values with Chris (checkpoint 6). Record
   the table in metrics.md under a Rounds section with the caveats:
   n = 1, observational, retrospective, "since the change" never "the
   change caused".
   Check: a printed table of stretches, windows, lead time per change,
   false-alarm rate per signal, and detection lag.

## Step 7: demo scenarios (demo/make_scenarios.py; branch, Chris merges)

Every scenario is either date-shifted real data or SYNTHETIC with
obviously fake timestamps (2020-01-01 start), and each ships with a
companion JSON documented in demo/scenarios/README.md (the boundary with
Chris's replay seek and catch-up). The generator runs where the raw data
is, prints summaries only, and writes only into demo/scenarios/.

1. The six core scenarios (the_save, normal_night, failure,
   meal_context, rearm_low, high_spike): date-shifted real nights that
   replace the skeleton's synthetic placeholders. The Save must show the
   forecast crossing before the actual value does.
2. **Basal-change windows (Standing Cards, the detect / decide / verify
   beats):** for each of the 2 to 3 real basal increases, 14 nights
   before and 14 after, date-shifted, as basal_change_<n>.csv + .json.
   The companion JSON carries the inferred reason codes (labeled
   inferred), the inferred alarm events, and a labeled synthetic overlay
   for what history cannot give: acknowledge times and ack_source,
   escalated flags, presence_during, and one recall answer per nocturnal
   low (real where Chris remembers, checkpoint 7; leave at least one as
   no answer so the card shows that row honestly), plus the confirmed
   dose change on its real date so the Follow-up card can be built after
   the seek. Chosen so the Basal Check rule fires before the change on
   the real data; the headline is computed by code, so whatever the
   numbers come out as goes into DEMO_SCRIPT.md, not the other way
   round.
3. **The synthetic titration scenario (Step Watch):** about 3 weeks
   covering a 14-night baseline, step 1, and step 2 days 3-7, with the
   plan (tirzepatide 2.5 → 5 mg, 4-week steps), symptom checks (rough on
   3 of 5 days in the step 2 window), weekly injection logs, recall
   answers (one step 2 low answered don't remember), and an alarm-event
   overlay (2 near-misses in that window), with insulin sensitivity
   drifting faster than the reduction at step 2, built so the seek and
   catch-up yield step 1 green cards and the step 2 amber card with
   EXACTLY the worked example: coverage 1,390 of 1,440; baseline median
   low point 98, window 76; TBR 58 of 1,440; 2 near-misses; 1 low not
   remembered; rough 3 of 5. Labeled SYNTHETIC in the JSON, the README,
   and the card. If a teammate or consenting volunteer is on a GLP-1,
   real date-shifted data replaces it.
4. Optional (Night Buddy): a night tuned so the ladder demos cleanly at
   60x: warning at T−30, crossing at T, nothing acknowledged through T+10
   (buddy rung) and T+20 (emergency script), about 20 s of wall time.
   Check: every scenario loads and plays at 60x without error, and the
   seek to any step produces the expected cards (Chris runs this; you
   read the printed result).

## Step 8: metrics.md, the Q&A sheet

Core section unchanged (step 3.7). Rounds section: what is real
(glucose, inferred reason codes and events, near-misses, the three
measured numbers), what is a labeled overlay (acks, escalation,
presence, morning answers), what is synthetic (the titration scenario),
the thresholds and any changes from the starting values, the leakage
guard's answer for each demo window, and the caveats in the spec's own
words. The Basal Check demo is described with this qualifier, verbatim,
every time: "glucose real; reason codes inferred and labeled;
acknowledge, presence, and recall data are a labeled overlay"; never an
unqualified "built from real nights". Keep the headline numbers in one
clearly marked block at the top, because the public mirror built on
submission day trims this file to that block. Night Buddy adds nothing
here.

## Step 9: Tiger Cloud, the dashboards' SQL, and the agreement test (cloud/sql/, C2)

Decided 2026-09-25 evening. You own cloud/sql/: the hypertables and
continuous aggregates every My Irin chart is drawn from. Rule: these draw
pictures and never decide; every card number still comes from nights.py,
and the agreement test below is what keeps the two honest.

1. `001_hypertables.sql`: readings(time timestamptz, device_id text,
   mgdl real, trend text, source text, is_demo bool) with
   create_hypertable on time and an index on (device_id, time desc);
   alarm_events, low_events, treatments the same way, mirroring the
   contracts fields. Primary key (device_id, time) so the Pi's retries
   upsert instead of duplicating.
2. `002_aggregates.sql`, continuous aggregates with refresh policies:
   daily_stats (time_bucket('1 day'): count, min, max, avg, share under
   70, in 70-180, over 180, per device); overnight_profile
   (time_bucket('30 minutes') across the last 14 days with
   percentile_agg(mgdl) so dash.py reads approx_percentile 0.1 / 0.5 /
   0.9 by time of day); nightly (time_bucket('1 day', time, origin
   22:00 local): coverage = count ÷ 108, low point = min of a 15-minute
   rolling median computed in the view, minutes under 70, first and last
   reading); hourly_heatmap (hour of day × day of week: share under 70);
   alarms_weekly and near_misses_weekly from alarm_events; basal_timing
   from treatments (kind basal, minutes from the usual time). Sensor
   gaps come from time_bucket_gapfill over readings at query time.
3. `003_policies.sql`: a compression policy on readings after 7 days
   (segment by device_id), retention none (the history stays), refresh
   policies every 30 minutes for the aggregates.
4. `ml/load_history.py`: loads the cleaned 22 months (ml/data/clean.csv)
   into readings under Chris's device_id from the laptop where the data
   lives, batched, printing counts only; run once (C5). Never from the
   Pi, never from the repo.
5. `ml/tests/test_agreement.py`: load one scenario night into TIGER_URI
   (the Tiger Cloud service, in a scratch schema the test creates and
   drops; you need no Docker) or, when Chris runs it, into his compose's
   timescaledb, and through nights.py, and assert the nightly aggregate
   and night_metrics agree on coverage, low point, and minutes under 70
   to the reading. This test is what invariant 21 points at. Check:
   green against TIGER_URI from your laptop and in Chris's compose when
   he runs it.
6. metrics.md gains one line: the dashboards' numbers come from SQL, the
   cards' from nights.py, and the agreement test proves they match where
   they overlap.

## Narratives (one pointer)

Your laptop runs NARRATIVE_BACKEND=template; you hand back the Backboard
key and the routing line, nothing else. The one narrative text that is
yours to draft is the second-opinion prompt (narrative.py's
second_opinion task: a different provider from the writer, JSON
{invented_number, advice}); DM the prompt text to Chris, whose lane
narrative.py is. Nothing else in the narrative chain touches ml/.

## Shared feature code (prevents train/serve skew)

Features are computed by ONE function living in ml/models/features.py,
imported by BOTH build_dataset.py (training) and predict.py (on the
Pi). Never re-implement a feature in two places: if training and
inference compute a lag or rolling window even slightly differently,
the model silently receives inputs it never trained on and its
predictions degrade with no error. Use vectorized pandas operations
(shift, rolling), not Python loops, so the build stays in seconds. The
same principle governs nights.py: one implementation of each metric,
shared by the ledger on the Pi and the validation scripts here.

## predict.py contract

Loads forecast_v1.json once at import; exposes a pure function taking
the recent reading window and returning the predicted 30-min-ahead
value (delta added back). No I/O per call, no globals mutated. Signature
changes are announced via journal Interface changes and coordinated
with Chris. nights.py follows the same contract rules.

The artifact crosses an architecture line: you train on a laptop, the
Pi runs aarch64. XGBoost JSON crosses it; a pickle does not. So: the
shipped model is always XGBoost JSON saved with the exact xgboost
version pinned in both requirements files; if the scikit-learn GBR wins
the comparison, retrain the winner in XGBoost with matching settings
rather than shipping a joblib file; if the linear baseline ships, it is
its coefficients in JSON. predict.py imports only numpy and xgboost
(no pandas, no scikit-learn on the Pi), and *.pkl / *.joblib are never
committed. Check: Chris loads forecast_v1.json on the Pi and
predict() returns the same number the laptop gets for one fixed window.

## Guardrails

No future information in any feature; dedupe before everything; never
interpolate; never train or forecast across a gap; raw CSVs, the
therapy-change list, and the labeled-nights file never leave ml/data/;
raw rows never enter Claude's context (summaries only); date-shift
anything real before it touches demo/scenarios/; every history-derived
label is marked inferred; every overlay field is labeled synthetic;
never fabricate acknowledge, presence, or morning-answer data into the
history itself.
