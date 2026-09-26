-- 001_hypertables.sql (George, docs/plans/george.md step 9.1) — C2
-- readings(time timestamptz, device_id text, mgdl real, trend text, source text, is_demo bool)
--   with create_hypertable on time and an index on (device_id, time desc);
--   primary key (device_id, time) so the Pi's retries upsert instead of duplicating.
-- alarm_events, low_events, treatments the same way, mirroring the contracts fields.
-- STUB: George fills this in. Kept syntactically empty so migrate.py can run.
SELECT 1;
