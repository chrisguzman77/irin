# Irin Cloud (`cloud/`)

The separate service that holds readings, events, and dashboards, so the
relay never does (invariant 20). **Nothing in cloud/ is ever read by a
card, a rule, or an alarm** (invariant 21): dashboards draw pictures and
never decide; the Pi keeps working with no cloud at all. `cloud/sql/` is
George's (the hypertables and continuous aggregates); the rest is Chris's.

## Ingest payload (C1) — `POST /v1/ingest`

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

`cloud/tests/test_ingest.py` creates these four tables as plain tables in a
scratch schema (the same columns, no hypertable) and needs a reachable
TIGER_URI (the compose file's timescaledb, or a scratch schema on Tiger
Cloud); it skips otherwise.

## Dashboard endpoints (C3) — `GET /v1/dash/{name}?days=` (owner bearer)

| name | aggregate (cloud/sql/002_aggregates.sql) |
|---|---|
| nights | nightly (reason-code tiles) |
| tir | daily_stats |
| profile | overnight_profile (percentile_agg 0.1 / 0.5 / 0.9) |
| lows_heatmap | hourly_heatmap |
| alarms | alarms_weekly |
| near_misses | near_misses_weekly |
| basal | basal_timing + nightly |
| sensor | readings via time_bucket_gapfill |
| step_watch | nightly against the plan windows |
| buddy | buddy events (relay metadata only) |
| under_the_hood | hypertable_compression_stats, counts, last sync |

Where an aggregate and `ml/models/nights.py` compute the same number,
`ml/tests/test_agreement.py` asserts they agree.

## Family (C3)

`GET /v1/family/last_night` (family bearer): low, high, minutes under 70,
current value, stale flag, the last-night strip. `POST | DELETE
/v1/family/bearers` (PIN, from the app's Family section); a revoked bearer
gets 401.

## Audio (B4+) — `POST /v1/audio/render` (relay key or owner bearer)

`{kind, text, voice}` -> `{audio_url}`; rendered once per text hash into
`cloud/audio_cache/` (gitignored), never re-rendered.

## Migrations

`python migrate.py` applies `sql/*.sql` in order, once, idempotently
(`schema_migrations`). The compose file runs it at boot.
