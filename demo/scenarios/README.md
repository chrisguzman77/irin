# Demo scenarios

Real scenarios are Chris's glucose, date-shifted by whole days (so the time of day, which
the forecaster uses, is unchanged); the real dates and the offsets never enter the repo.
Synthetic scenarios have obviously fake timestamps (2020) and say SYNTHETIC in their
companion JSON and on every card. Regenerate everything with `python -m demo.make_scenarios`
(the real ones only where the raw data lives); ml/tests/test_scenarios.py checks each one.

The six core scenarios (George, step 7.1; real, date-shifted, nights from 2021-03-01, one
per day in this order; picked by stated criteria in make_scenarios.py; Chris vouched for
every real night, these six and basal_change_1, at checkpoint 7 on 2026-09-26):

- `the_save.csv`: a HELD-OUT overnight low (forecast_v1 never trained on it), so the
  device's live forecast is out-of-sample: the warning (85 on the 20th-percentile
  forecast) comes 25 min before the first reading under 70; 4 readings under 70. 40 rows
  (3 h 15 min), starting 1 h 35 min before the warning so the forecaster has its first
  hour, and trimmed to stay under the 250 high threshold at both ends (this real low was
  over-treated and rebounds past 250 afterwards; a high alert would muddy the two low
  tiers). It REPLACES the skeleton's 97-row placeholder: backend/tests/test_replay.py's
  constants (97 rows, min 55) must change to this file's (40 rows, min 50) in the same
  merge (Chris); test_alarm.py and test_smoke.py pass on it unchanged.
- `normal_night.csv`: a clean night, 90-180 mg/dL from 20:00 to 08:00, no warnings.
- `failure.csv`: a 63-min sensor gap mid-night; the display must go STALE and never
  forecast across it.
- `meal_context.csv`: an inferred late meal (+95 mg/dL in the first 2 h of the night),
  peak under 250, no lows.
- `rearm_low.csv`: an overnight low under 70 for 160 min, so an acknowledged low re-arms
  every 15 min; the recovery is in the file. IN-SAMPLE: forecast_v1 trained on this night,
  so its warnings here demonstrate the re-arm, not forecast accuracy.
- `high_spike.csv`: above 250 for 60 min (peak 274), back under 200 by morning: the
  one-shot high alert.

- `titration_synthetic.csv` + `.json` (George, step 7.3): 49 SYNTHETIC days from
  2020-01-01, a 14-night baseline, tirzepatide step 1 (2.5 mg, 4 weeks), step 2 (5 mg)
  through days 3-7 (days 44-48). Over step 2 days 3-7 against the baseline it gives the
  chris.md R10 worked example exactly through ml/models/nights.py (coverage 1,390 / 1,440,
  low point 98 -> 76, TBR 58 / 1,440, 2 near-misses, 1 low answered don't remember, rough
  3 of 5); step 1 stays green. Regenerate with `python -m demo.make_scenarios`;
  ml/tests/test_scenarios.py locks every number. (The plan said "about 3 weeks"; with
  4-week steps the baseline, step 1, and step 2 days 3-7 take 49 days.)
- `basal_change_1.csv` + `.json` (George, step 7.2): REAL glucose around Chris's first
  confirmed basal increase, date-shifted so the change lands on 2021-02-01 (the real
  date and the offset stay off-repo): 21 nights before, 14 after. Reason codes are
  inferred; alarm events come from forecast_v1 RETRAINED WITHOUT this window (leakage
  guard, george.md step 6.4), replayed at 85; acks, escalation, presence, and recall
  answers are a SYNTHETIC overlay (`overlay_synthetic: true`), and the one nocturnal low
  is left unanswered. Basal Check fires every morning 2021-01-25..29 (8-9 clean nights,
  median rise +84 to +93, 75-78% rising); the Follow-up has 6 clean nights in days 1-7
  (still rising, +78.5) and 11 in days 1-14 (flat, +2). `dose_change.new_units` = 18 was
  given by George; whether it is Chris's real dose is unconfirmed, so say "units
  illustrative" until he confirms.

## Companion JSON schema (`<name>.json` beside `<name>.csv`)

Every scenario MAY ship a companion. Chris's replay seek and catch-up (R12)
read it; George's `make_scenarios.py` writes it. Models are the ones in
`backend/app/contracts.py`.

```json
{
  "scenario": "the_save",
  "kind": "core",                    // core | basal_change | titration | buddy
  "synthetic": true,                 // the glucose itself is synthetic
  "overlay_synthetic": true,         // acks, presence, recall answers are a labeled overlay
  "night_window": {"start": "22:00", "end": "07:00"},
  "reason_codes": {                  // per night: codes and code_source ("inferred" on history)
    "2020-01-01": {"codes": ["clean"], "code_source": "inferred"}
  },
  "plan": null,                      // TitrationPlan | null
  "dose_change": null,               // {date, insulin, new_units} | null — the confirmed change the Follow-up compares around
  "symptom_checks": [],              // list[SymptomCheck]
  "injections": [],                  // list[Treatment] with kind glp1_dose and dose_label
  "recall_answers": {},              // low_event_id -> answer | null (null = no answer, never fine)
  "alarm_events": []                 // list[AlarmEvent]: glucose-side fields inferred from the data, response-side fields an overlay
}
```

Rules: reason codes on history are `inferred` and labeled so, never presented
as logged; the overlay never fabricates acknowledge, presence, or morning
answers into the history itself; a titration scenario is SYNTHETIC unless a
consenting volunteer's date-shifted data replaces it.
