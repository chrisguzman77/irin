-- 004_family_bearers_realtime.sql (George, C3; 001-003 are frozen, applied to Tiger)
-- 1. family_bearers: the revocable read-only tokens GET /v1/family/last_night
--    accepts (contracts.FamilyBearer + the device they read). Only a SHA-256
--    hash of each token is stored; the token itself is shown once, at creation.
-- 2. Real-time continuous aggregates: recent readings (the live Pi forwards
--    every ~5 min) show on the dashboards before the next 30-min refresh.

CREATE TABLE IF NOT EXISTS family_bearers (
    bearer_id    text PRIMARY KEY,
    recipient_id text NOT NULL,
    device_id    text NOT NULL,
    token_hash   text NOT NULL UNIQUE,
    created_at   timestamptz NOT NULL DEFAULT now(),
    revoked_at   timestamptz
);

ALTER MATERIALIZED VIEW daily_stats       SET (timescaledb.materialized_only = false);
ALTER MATERIALIZED VIEW overnight_profile SET (timescaledb.materialized_only = false);
ALTER MATERIALIZED VIEW hourly_heatmap    SET (timescaledb.materialized_only = false);
ALTER MATERIALIZED VIEW alarms_weekly     SET (timescaledb.materialized_only = false);
