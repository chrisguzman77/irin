-- 001_hypertables.sql (George, docs/plans/george.md step 9.1, C2)
-- The four tables cloud/ingest.py writes, EXACTLY the column contract in
-- cloud/README.md (Chris, C1): naive LOCAL timestamps (timestamp without time
-- zone, so the 22:00-origin nights need no time-zone arithmetic), and every
-- primary key includes the time column (a hypertable's unique indexes must),
-- which is what ingest's ON CONFLICT upserts target. Each becomes a
-- hypertable on its time column. Runs inside migrate.py's one transaction.

CREATE TABLE IF NOT EXISTS readings (
    device_id text NOT NULL,
    time      timestamp NOT NULL,
    mgdl      real NOT NULL,
    trend     text,
    source    text,
    is_demo   bool NOT NULL DEFAULT false,
    PRIMARY KEY (device_id, time)
);
SELECT create_hypertable('readings', by_range('time', INTERVAL '7 days'), if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS readings_device_time_desc ON readings (device_id, time DESC);

CREATE TABLE IF NOT EXISTS alarm_events (
    device_id       text NOT NULL,
    started_at      timestamp NOT NULL,
    event_id        text NOT NULL,
    tier            text NOT NULL,
    acknowledged_at timestamp,
    ack_source      text,
    escalated       bool NOT NULL DEFAULT false,
    rearm_count     int NOT NULL DEFAULT 0,
    crossed_actual  bool NOT NULL DEFAULT false,
    presence_during text,
    is_demo         bool NOT NULL DEFAULT false,
    PRIMARY KEY (device_id, started_at, event_id)
);
SELECT create_hypertable('alarm_events', by_range('started_at', INTERVAL '30 days'), if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS low_events (
    device_id                 text NOT NULL,
    started_at                timestamp NOT NULL,
    low_event_id              text NOT NULL,
    night_date                date,
    nadir_mgdl                real,
    nadir_at                  timestamp,
    minutes_below_70          int,
    auc_below_70              real,
    recovery_slope            real,
    carbs_logged_within_30min bool,
    inferred_unfelt           bool,
    alarm_event_id            text,
    is_demo                   bool NOT NULL DEFAULT false,
    PRIMARY KEY (device_id, started_at, low_event_id)
);
SELECT create_hypertable('low_events', by_range('started_at', INTERVAL '30 days'), if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS treatments (
    device_id     text NOT NULL,
    time          timestamp NOT NULL,
    kind          text NOT NULL,
    insulin_units real,
    carbs_g       real,
    dose_label    text,
    text          text,
    confirmed     bool,
    is_demo       bool NOT NULL DEFAULT false,
    PRIMARY KEY (device_id, time, kind)
);
SELECT create_hypertable('treatments', by_range('time', INTERVAL '30 days'), if_not_exists => TRUE);
