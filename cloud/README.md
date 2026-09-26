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
row per reading. Demo readings carry is_demo and land under a separate demo
device_id so real dashboards never show replay data.

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
