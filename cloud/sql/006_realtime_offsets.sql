-- 006_realtime_offsets.sql (George's lane, stepped in by Chris; 001-005 are frozen)
-- The Pi's timestamps are naive LOCAL time (America/New_York, 4-5 h behind
-- UTC), but a refresh policy's end_offset is measured from Tiger's now(),
-- which is UTC. With 003's 30-60 min offsets every refresh materialized
-- buckets up to ~3 h AHEAD of the device's clock, so 004's real-time
-- aggregates never covered fresh readings: a reading forwarded after a
-- refresh stayed invisible until the next one (seen on the hosted cloud:
-- daily_stats 93 of 98 readings). end_offset 6 hours (the zone's largest
-- offset, 5 h, plus 1 h) stops materializing behind the device's local time
-- and the real-time union draws the rest. Same 30-minute schedule.

SELECT remove_continuous_aggregate_policy('daily_stats', if_exists => TRUE);
SELECT remove_continuous_aggregate_policy('overnight_profile', if_exists => TRUE);
SELECT remove_continuous_aggregate_policy('hourly_heatmap', if_exists => TRUE);
SELECT remove_continuous_aggregate_policy('alarms_weekly', if_exists => TRUE);

SELECT add_continuous_aggregate_policy('daily_stats',
    start_offset => NULL, end_offset => INTERVAL '6 hours', schedule_interval => INTERVAL '30 minutes');
SELECT add_continuous_aggregate_policy('overnight_profile',
    start_offset => NULL, end_offset => INTERVAL '6 hours', schedule_interval => INTERVAL '30 minutes');
SELECT add_continuous_aggregate_policy('hourly_heatmap',
    start_offset => NULL, end_offset => INTERVAL '6 hours', schedule_interval => INTERVAL '30 minutes');
SELECT add_continuous_aggregate_policy('alarms_weekly',
    start_offset => NULL, end_offset => INTERVAL '6 hours', schedule_interval => INTERVAL '30 minutes');
