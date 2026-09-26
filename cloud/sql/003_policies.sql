-- 003_policies.sql (George, docs/plans/george.md step 9.3, C2)
-- Compression on readings after 7 days, segmented by device (the history
-- stays: no retention policy anywhere), and a 30-minute refresh for every
-- continuous aggregate. The first refresh materializes the history the
-- aggregates were created without (002 uses WITH NO DATA).

ALTER TABLE readings SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'device_id',
    timescaledb.compress_orderby = 'time DESC'
);
SELECT add_compression_policy('readings', INTERVAL '7 days', if_not_exists => TRUE);

SELECT add_continuous_aggregate_policy('daily_stats',
    start_offset => NULL, end_offset => INTERVAL '1 hour', schedule_interval => INTERVAL '30 minutes',
    if_not_exists => TRUE);
SELECT add_continuous_aggregate_policy('overnight_profile',
    start_offset => NULL, end_offset => INTERVAL '30 minutes', schedule_interval => INTERVAL '30 minutes',
    if_not_exists => TRUE);
SELECT add_continuous_aggregate_policy('hourly_heatmap',
    start_offset => NULL, end_offset => INTERVAL '1 hour', schedule_interval => INTERVAL '30 minutes',
    if_not_exists => TRUE);
SELECT add_continuous_aggregate_policy('alarms_weekly',
    start_offset => NULL, end_offset => INTERVAL '1 hour', schedule_interval => INTERVAL '30 minutes',
    if_not_exists => TRUE);
