-- 002_aggregates.sql (George, docs/plans/george.md step 9.2, C2)
-- What the My Irin dashboards draw from. They draw pictures and never
-- decide (invariant 21): no card, rule, or alarm reads any of these. Where
-- one computes a number ml/models/nights.py also computes, the definitions
-- are the same and ml/tests/test_agreement.py asserts they agree.
-- dash.py and the charts read these NAMES and COLUMNS; change them only with
-- a journal note.
--
-- Continuous aggregates are created WITH NO DATA (TimescaleDB cannot
-- materialize one inside a transaction, and migrate.py applies each file in
-- one); 003_policies.sql's refresh policies fill them.
--
-- Two dashboards are PLAIN VIEWS on purpose, because a continuous aggregate
-- allows no window functions, subqueries, or joins between hypertables:
--   nightly            the low point is the min of a 15-min ROLLING median
--                      (nights.py), which needs a per-reading lookback;
--   near_misses_weekly a near-miss needs "no reading under 70 within 60 min"
--                      from readings, a second hypertable.
-- Both are cheap at this scale (one person, a few hundred nights).

-- daily_stats: one row per device per calendar day.
--   day, readings, min_mgdl, max_mgdl, avg_mgdl,
--   share_under_70, share_70_180, share_over_180 (0..1 of readings present)
CREATE MATERIALIZED VIEW IF NOT EXISTS daily_stats WITH (timescaledb.continuous) AS
SELECT device_id,
       time_bucket(INTERVAL '1 day', time) AS day,
       count(*)                                                   AS readings,
       min(mgdl)                                                  AS min_mgdl,
       max(mgdl)                                                  AS max_mgdl,
       avg(mgdl)                                                  AS avg_mgdl,
       avg(CASE WHEN mgdl < 70 THEN 1.0 ELSE 0.0 END)                 AS share_under_70,
       avg(CASE WHEN mgdl >= 70 AND mgdl <= 180 THEN 1.0 ELSE 0.0 END) AS share_70_180,
       avg(CASE WHEN mgdl > 180 THEN 1.0 ELSE 0.0 END)                AS share_over_180
FROM readings
GROUP BY device_id, day
WITH NO DATA;

-- overnight_profile: 30-min buckets with a percentile sketch. dash.py takes
-- the last 14 days, groups by time of day, and reads
--   approx_percentile(0.1 / 0.5 / 0.9, rollup(pct)).
CREATE MATERIALIZED VIEW IF NOT EXISTS overnight_profile WITH (timescaledb.continuous) AS
SELECT device_id,
       time_bucket(INTERVAL '30 minutes', time) AS bucket,
       percentile_agg(mgdl::double precision)    AS pct,
       count(*)                                  AS readings
FROM readings
GROUP BY device_id, bucket
WITH NO DATA;

-- hourly_heatmap: hourly counts; dash.py groups by extract(hour) x
-- extract(dow) of `hour` and draws sum(under_70) / sum(readings).
CREATE MATERIALIZED VIEW IF NOT EXISTS hourly_heatmap WITH (timescaledb.continuous) AS
SELECT device_id,
       time_bucket(INTERVAL '1 hour', time)            AS hour,
       count(*)                                        AS readings,
       sum(CASE WHEN mgdl < 70 THEN 1 ELSE 0 END)      AS under_70
FROM readings
GROUP BY device_id, hour
WITH NO DATA;

-- alarms_weekly: per device, week, and tier.
--   week, tier, alarms, escalated, rearms, mean_ack_min (acknowledged only)
CREATE MATERIALIZED VIEW IF NOT EXISTS alarms_weekly WITH (timescaledb.continuous) AS
SELECT device_id,
       time_bucket(INTERVAL '7 days', started_at)                   AS week,
       tier,
       count(*)                                                     AS alarms,
       sum(CASE WHEN escalated THEN 1 ELSE 0 END)                   AS escalated,
       sum(rearm_count)                                             AS rearms,
       avg(EXTRACT(EPOCH FROM (acknowledged_at - started_at)) / 60) AS mean_ack_min
FROM alarm_events
GROUP BY device_id, week, tier
WITH NO DATA;

-- nightly: one row per device per night (the evening's date), 22:00-07:00,
-- EXACTLY nights.night_metrics' definitions:
--   coverage_pct     = min(100, 100 x readings / 108)          (9 h x 12)
--   low_point_mgdl   = min over the night's readings of the median of the
--                      readings in (t - 15 min, t] within the same night,
--                      counted only where there are >= 2 of them
--   minutes_below_70 = 5 x readings under 70
--   first_reading, last_reading
CREATE OR REPLACE VIEW nightly AS
WITH w AS (
    SELECT device_id, time, mgdl,
           CASE WHEN time::time >= TIME '22:00' THEN time::date ELSE (time::date - 1) END AS night_date
    FROM readings
    WHERE time::time >= TIME '22:00' OR time::time < TIME '07:00'
)
SELECT w.device_id,
       w.night_date,
       count(*)                                                   AS readings,
       LEAST(100.0, 100.0 * count(*) / 108)                       AS coverage_pct,
       min(m.med15)                                               AS low_point_mgdl,
       5 * sum(CASE WHEN w.mgdl < 70 THEN 1 ELSE 0 END)           AS minutes_below_70,
       min(w.time)                                                AS first_reading,
       max(w.time)                                                AS last_reading
FROM w
LEFT JOIN LATERAL (
    SELECT CASE WHEN count(*) >= 2 THEN percentile_cont(0.5) WITHIN GROUP (ORDER BY r.mgdl) END AS med15
    FROM readings r
    WHERE r.device_id = w.device_id
      AND r.time >  w.time - INTERVAL '15 minutes'
      AND r.time <= w.time
      AND r.time >= w.night_date + TIME '22:00'
) m ON TRUE
GROUP BY w.device_id, w.night_date;

-- near_misses_weekly: predicted-low warnings that did not cross AND had no
-- reading under 70 within 60 min of their start (nights.near_misses).
--   week, near_misses
CREATE OR REPLACE VIEW near_misses_weekly AS
SELECT a.device_id,
       time_bucket(INTERVAL '7 days', a.started_at) AS week,
       count(*)                                     AS near_misses
FROM alarm_events a
WHERE a.tier = 'predicted_low'
  AND NOT a.crossed_actual
  AND NOT EXISTS (
      SELECT 1 FROM readings r
      WHERE r.device_id = a.device_id
        AND r.time >= a.started_at
        AND r.time <= a.started_at + INTERVAL '60 minutes'
        AND r.mgdl < 70)
GROUP BY a.device_id, week;

-- basal_timing: every logged basal dose with its time of day in minutes;
-- dash.py draws it against the patient's usual basal time (a device setting
-- the cloud does not hold).  time, minutes_of_day, insulin_units, confirmed
CREATE OR REPLACE VIEW basal_timing AS
SELECT device_id,
       time,
       (EXTRACT(HOUR FROM time) * 60 + EXTRACT(MINUTE FROM time))::int AS minutes_of_day,
       insulin_units,
       confirmed
FROM treatments
WHERE kind = 'basal';
