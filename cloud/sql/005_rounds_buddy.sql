-- 005_rounds_buddy.sql (George's lane, stepped in by Chris; 001-004 are frozen)
-- The four lists the Pi's forwarder adds to the ingest payload so the Step
-- Watch and Night Buddy dashboards can draw (column contract in
-- cloud/README.md). Same rules as 001: naive LOCAL timestamps, every primary
-- key includes the time column, ingest upserts on it. night_records and
-- buddy_events grow with time and become hypertables; plans and
-- symptom_checks are a handful of rows and stay plain tables. Nothing here is
-- read by a card, a rule, or an alarm (invariant 21).

CREATE TABLE IF NOT EXISTS night_records (
    device_id        text NOT NULL,
    night_date       date NOT NULL,
    window_start     timestamp NOT NULL,
    window_end       timestamp NOT NULL,
    coverage_pct     real NOT NULL,
    reason_codes     text[] NOT NULL DEFAULT '{}',
    code_source      text NOT NULL,
    rise_mgdl        real,
    low_point_mgdl   real,
    tbr_pct          real,
    minutes_below_70 int NOT NULL DEFAULT 0,
    near_miss_count  int NOT NULL DEFAULT 0,
    level2_count     int NOT NULL DEFAULT 0,
    is_demo          bool NOT NULL DEFAULT false,
    PRIMARY KEY (device_id, night_date)
);
SELECT create_hypertable('night_records', by_range('night_date', INTERVAL '30 days'), if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS plans (
    device_id   text NOT NULL,
    started_at  date NOT NULL,
    plan_id     text NOT NULL,
    drug_class  text NOT NULL,
    drug_label  text NOT NULL,
    steps       jsonb NOT NULL DEFAULT '[]',
    status      text NOT NULL,
    is_demo     bool NOT NULL DEFAULT false,
    PRIMARY KEY (device_id, started_at, plan_id)
);

CREATE TABLE IF NOT EXISTS symptom_checks (
    device_id text NOT NULL,
    date      date NOT NULL,
    gi        text NOT NULL,
    is_demo   bool NOT NULL DEFAULT false,
    PRIMARY KEY (device_id, date)
);

CREATE TABLE IF NOT EXISTS buddy_events (
    device_id  text NOT NULL,
    at         timestamp NOT NULL,
    event_id   text NOT NULL,
    kind       text NOT NULL,        -- alert | claim | call | treating | resolved
    confidence text,                 -- device_confirmed | unconfirmed
    is_demo    bool NOT NULL DEFAULT false,
    PRIMARY KEY (device_id, at, event_id)
);
SELECT create_hypertable('buddy_events', by_range('at', INTERVAL '30 days'), if_not_exists => TRUE);
