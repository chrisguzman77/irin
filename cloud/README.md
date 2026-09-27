# Irin Cloud (`cloud/`)

The separate service that holds readings, events, and dashboards, so the
relay never does (invariant 20). **Nothing in cloud/ is ever read by a
card, a rule, or an alarm** (invariant 21): dashboards draw pictures and
never decide; the Pi keeps working with no cloud at all. `cloud/sql/` is
George's (the hypertables and continuous aggregates); the rest is Chris's.

## Ingest payload (C1) â€” `POST /v1/ingest`

Headers: `X-Device-Id`, `X-Device-Token` (validated against cloud's own
DEVICE_ID / DEVICE_TOKEN; one hackathon device). Body:

```json
{
  "device_id": "irin-dev-0001",
  "readings":     [Reading, ...],       // contracts.Reading + is_demo
  "alarm_events": [AlarmEvent, ...],
  "low_events":   [LowEvent, ...],
  "treatments":   [Treatment, ...]
}
```

Upsert on (device_id, timestamp): sending the same batch twice stores one
row per reading. Rows with is_demo land under `<device_id>-demo` (the cloud
routes them, the Pi only flags them) so real dashboards never show replay
data. The body's device_id must equal the header's. 503 when Tiger is
unreachable: the Pi keeps its cursor and retries next tick.

### Column contract (cloud/ingest.py writes, cloud/sql/001 creates)

Timestamps are the Pi's naive LOCAL time exactly as its API serves them, in
`timestamp` (without time zone) columns, so the 22:00-origin night buckets
need no time-zone arithmetic. Every primary key includes the time column
(a hypertable's unique indexes must), and the upsert conflicts on it.

| table | columns | primary key |
|---|---|---|
| readings | device_id text, time timestamp, mgdl real, trend text, source text, is_demo bool | (device_id, time) |
| alarm_events | device_id, started_at timestamp, event_id text, tier text, acknowledged_at timestamp, ack_source text, escalated bool, rearm_count int, crossed_actual bool, presence_during text, is_demo bool | (device_id, started_at, event_id) |
| low_events | device_id, started_at timestamp, low_event_id text, night_date date, nadir_mgdl real, nadir_at timestamp, minutes_below_70 int, auc_below_70 real, recovery_slope real, carbs_logged_within_30min bool, inferred_unfelt bool, alarm_event_id text, is_demo bool | (device_id, started_at, low_event_id) |
| treatments | device_id, time timestamp, kind text, insulin_units real, carbs_g real, dose_label text, text text, confirmed bool, is_demo bool | (device_id, time, kind) |
| night_records | device_id, night_date date, window_start timestamp, window_end timestamp, coverage_pct real, reason_codes text[], code_source text, rise_mgdl real, low_point_mgdl real, tbr_pct real, minutes_below_70 int, near_miss_count int, level2_count int, is_demo bool | (device_id, night_date) |
| plans | device_id, started_at date, plan_id text, drug_class text, drug_label text, steps jsonb, status text, is_demo bool | (device_id, started_at, plan_id) |
| symptom_checks | device_id, date date, gi text, is_demo bool | (device_id, date) |
| buddy_events | device_id, at timestamp, event_id text, kind text, confidence text, is_demo bool | (device_id, at, event_id) |

The last four (cloud/sql/005) carry the Step Watch and Night Buddy
dashboards. They are the contracts objects' fields as serialized:
`night_records` from NightRecord (reason_codes a text array; the upsert
replaces a re-coded night), `plans` from TitrationPlan (steps = the
`[{index, dose_label, planned_start}]` list as JSON; the upsert replaces
status), `symptom_checks` from SymptomCheck (the upsert replaces the day's
answer, like the Pi's second tap), `buddy_events` {event_id, kind: alert |
claim | call | treating | resolved, at, confidence: device_confirmed |
unconfirmed}. None of them holds a glucose value beyond a night's low point.

`cloud/tests/test_ingest.py` creates the first four tables as plain tables in a
scratch schema (the same columns, no hypertable) and needs a reachable
TIGER_URI (the compose file's timescaledb, or a scratch schema on Tiger
Cloud); it skips otherwise.

## Dashboard endpoints (C3) — `GET /v1/dash/{name}?days=&demo=` (owner bearer)

Auth: `Authorization: Bearer <OWNER_BEARER>`; 401 without or wrong, 503 (fail
closed) while OWNER_BEARER is unset. `days` 1..3650 (default 14). `demo=true`
reads `<DEVICE_ID>-demo`, so replayed data is never drawn as the real device.
The window ENDS at the device's newest reading (`as_of`), not the server clock,
so replayed demo data and history draw the same way.

Every response carries `{name, device_id, is_demo, days, as_of, available,
empty}`, then the chart's fields. `empty: true` (with `rows: []`) means no data:
the chart says so and never draws zeros. Timestamps are the Pi's naive local time.

| name | reads | rows (one per) and fields |
|---|---|---|
| nights | `nightly` view | night: night_date, readings, coverage_pct, low_point_mgdl, minutes_below_70, first_reading, last_reading; `reason_codes: null` (not forwarded) |
| tir | `daily_stats` | day: day, readings, avg_mgdl, share_under_70, share_70_180, share_over_180 (0..1 of readings PRESENT), in_range_7d, in_range_30d |
| profile | `overnight_profile` | 30-min time of day: time_of_day, p10, p50, p90, readings |
| lows_heatmap | `hourly_heatmap` | weekday (ISO 1 = Mon) x hour_of_day: under_70, readings, share_under_70 |
| alarms | `alarms_weekly` | week x tier: week, tier, alarms, escalated, rearms, mean_ack_min |
| near_misses | `near_misses_weekly` | week: week, near_misses |
| basal | `basal_timing` | dose: time, minutes_of_day, insulin_units, confirmed; `usual_time: null` (a device setting) |
| sensor | readings, `time_bucket_gapfill` | day: day, slots_with_reading, gap_minutes, coverage_pct (of 288) |
| under_the_hood | counts, compression stats, jobs | one object: readings, first_reading, last_reading, last_sync, readings_table_bytes_all_devices, compressed_before_bytes, compressed_after_bytes, compression_ratio, last_aggregate_refresh |
| step_watch | `night_records`, `plans`, `symptom_checks`, treatments (kind glp1_dose) | see below |
| buddy | `buddy_events` | week: week, alerts, claims, calls, treating, resolved, alerts_device_confirmed, alerts_unconfirmed |

`step_watch` draws the newest plan whose status is not `pending_confirm`, with
the window's nights, check-ins, and shots. It returns `empty: true` when there
is no plan or no night in the window:

```json
{ ...common fields,
  "plan": {"plan_id": "p1", "drug_class": "glp1", "drug_label": "Semaglutide",
           "status": "active", "started_at": "2020-01-15",
           "steps": [{"index": 0, "dose_label": "0.25 mg", "planned_start": "2020-01-15"}]},  // null: no plan
  "baseline": {"from": "2020-01-01", "to": "2020-01-14",       // the 14 nights before started_at
               "low_point_mgdl": 88.0,                           // median low point of those nights with
               "nights": 12},                                    //   coverage >= 85% and no "stale" code; null if none
  "rows": [{"night_date": "2020-01-20", "coverage_pct": 97.2, "low_point_mgdl": 81.0,
            "vs_baseline_mgdl": -7.0,                            // low_point - baseline (null if either is)
            "minutes_below_70": 0, "tbr_pct": 0.0, "near_miss_count": 0, "level2_count": 0,
            "reason_codes": ["clean"], "code_source": "inferred"}],   // one per night in the window
  "checkins":   [{"date": "2020-01-20", "gi": "fine"}],          // fine | rough | cant_eat; a missing day is absent
  "injections": [{"time": "2020-01-15T08:00:00", "dose_label": "0.25 mg", "confirmed": true}] }
```

`baseline` is null with no plan. `baseline.low_point_mgdl` is the number
`nights.step_window_metrics` calls `baseline_low_point` (cloud/tests/test_dash.py
asserts they are equal). The chart draws it as a reference line and never
colors a night by it (invariant 21: the card says what a shift means, not the
dashboard). `code_source` says whether reason codes were logged or inferred,
and the chart shows it.

`buddy` rows: one per 7-day bucket (time_bucket, like `alarms`) with a count
of each event kind. Alerts are split by confidence so device-confirmed and
unconfirmed are drawn distinctly (invariant 15). The table holds no names,
contacts, or glucose values to draw.

Invariant 21: dashboards draw pictures and never decide. `nights` equals
`ml/models/nights.py` (asserted by `ml/tests/test_agreement.py` and, through
this API, by `cloud/tests/test_dash.py`). `tir` is NOT a number nights.py
computes (share of readings present, the usual time in range) and is never
compared with a card.

## Family (C3)

`GET /v1/family/last_night` — `Authorization: Bearer <family token>`; 401 when
missing, unknown, or revoked. Fields (PINNED; the family page reads exactly these):

```json
{
  "device_id": "irin-pi-01", "is_demo": false,
  "as_of": "2026-09-26T18:50:00",                     // the device's local time now (DEVICE_TZ)
  "current": {"mgdl": 112.0, "trend": "Flat", "at": "2026-09-26T18:45:16",
              "minutes_since": 4.7, "stale": false},  // stale: newest reading older than 15 min
  "last_night": {"night_date": "2026-09-25",           // the evening the night starts on
                 "in_progress": false,
                 "low":  {"mgdl": 74.0,  "at": "2026-09-26T03:10:00"},
                 "high": {"mgdl": 181.0, "at": "2026-09-25T22:40:00"},
                 "minutes_below_70": 0, "coverage_pct": 98.1,
                 "strip": [{"time": "2026-09-25T22:00:12", "mgdl": 150.0}]}   // every reading 22:00-07:00
}
```
`current` or `last_night` is `null` when there is no data. The family page greys
the number when `stale` is true.

`POST /v1/family/bearers` (`X-PIN`) body `{"recipient_id": "mom", "demo": false}`
-> `{bearer_id, recipient_id, token, is_demo, created_at}`: the token is shown
ONCE; only its SHA-256 hash is stored (table `family_bearers`, cloud/sql/004).
`DELETE /v1/family/bearers?bearer_id=...` (`X-PIN`) -> `{bearer_id, revoked:
true}`, refused from the next request on; 404 if not active. 503 while PIN is
unset.

## Environment (C3 adds three; announced in journal/george.md)

`OWNER_BEARER` (the owner's dashboard token), `PIN` (the same PIN as the Pi's,
for family bearers), `DEVICE_TZ` (the Pi's zone for the stale flag; default
America/New_York). Unset OWNER_BEARER or PIN fails closed (503).

## Audio (B4+) â€” `POST /v1/audio/render` (relay key or owner bearer)

`{kind, text, voice}` -> `{audio_url}`; rendered once per text hash into
`cloud/audio_cache/` (gitignored), never re-rendered.

## Migrations

`python migrate.py` applies `sql/*.sql` in order, once, idempotently
(`schema_migrations`). The compose file runs it at boot.
