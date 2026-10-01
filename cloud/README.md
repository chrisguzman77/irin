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

Auth: one of the three credentials in "Phone accounts (Phase 1)" below, each
resolved to the device it draws; `Authorization: Bearer <OWNER_BEARER>` (the
admin backup) draws DEVICE_ID, 401 without or wrong, 503 (fail closed) while
OWNER_BEARER is unset. `days` 1..3650 (default 14). `demo=true` reads
`<device>-demo`, so replayed data is never drawn as the real device.
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

## Audio (B4+) â€” `POST /v1/audio/render`, `GET /v1/audio/{hash}.mp3`

`POST /v1/audio/render` (headers `X-Device-Id`, `X-Device-Token`: the same
check as /v1/ingest) body `{"kind": "buddy_alert", "text": "Your buddy Chris is
in trouble. The alarm has been unacknowledged for twelve minutes."}` ->
`{"audio_url": "https://cloud.<DOMAIN>/v1/audio/<64 hex>.mp3"}`. Only
`kind` and `text` (1-400 chars) are accepted (any other field, or another
kind, is 422); the voice is always `ELEVENLABS_VOICE_ALERT`. The Pi's buddy
rung sends minutes spelled out in words, never a digit.

- Rendered with ElevenLabs only when `VOICE_BACKEND=elevenlabs` (and the key
  and voice id are set); otherwise `{"audio_url": null}`.
- Cached by sha256 of (kind, voice, text) in `cloud/audio_cache/` (gitignored;
  the compose volume `audio_cache`): a cache hit makes no API call and the
  same text is never rendered twice.
- 422 when the text contains a digit followed by mg (`54 mg/dL`, `54mgdl`) or
  the word glucose, mg, mgdl, or dL: no glucose value is ever spoken to a buddy.
- 502 when ElevenLabs fails (nothing cached); 401 on a bad device token; 503
  while DEVICE_ID / DEVICE_TOKEN are unset. The Pi treats every non-200, and
  anything slower than 3 s, as no clip and sends the alert anyway.

`GET /v1/audio/{hash}.mp3` (public, no credential: the hash is unguessable, and
the watcher page and WhatsApp fetch it by link) -> `audio/mpeg`; 404 for an
unknown or malformed name. With `DOMAIN` unset or `localhost` the URL base is
`http://localhost:8200`.

## Phone accounts (Phase 1) â€” `POST /v1/accounts/verify` and the dashboard credentials

The cloud half of relay/README.md "Phone-only accounts (Phase 1), PINNED
2026-10-01" (that section is the contract; this mirrors it). A phone with no
Irin signs up at the relay, then verifies its CGM feed ONCE here; the relay
learns one boolean and never the feed URL, the token, or a value (invariant
20). Code: `cloud/accounts.py`; tests: `cloud/tests/test_accounts.py` (fake
Nightscout and fake relay over a MockTransport, never a live one).

Environment: `RELAY_URL` (compose: `http://relay:8100`) and `RELAY_CLOUD_KEY`
(one random string shared with the relay; the compose file passes both). The
feed URL and token are stored encrypted under `RELAY_KEY` (already passed to
the cloud): key = sha256(RELAY_KEY), nacl SecretBox (`pynacl`). Verify is 503
while RELAY_KEY or RELAY_CLOUD_KEY is unset.

Table `phone_accounts` (cloud/sql/007, a plain table; migrate.py applies it at
boot): `user_id text primary key, nightscout_url_enc bytea,
nightscout_token_enc bytea, verified_at timestamptz, dash_token_hash text
unique, created_at timestamptz default now()`. No glucose value is ever
stored in it.

`POST /v1/accounts/verify` (public; its own bucket of 5 per minute per IP,
429 past it) body `{user_id, user_bearer, nightscout_url, nightscout_token}`:
the URL is `https?://host[:port][/path]`, max 200 chars, no query string; the
token 1-200 chars; `user_id` is the relay's (`u-...`). Steps, in order:

1. `GET {nightscout_url}/api/v1/entries.json?count=1&token=<token>` (10 s
   timeout; the same auth the Pi's datasource uses). The feed is live when the
   reply is a 2xx JSON list whose first entry has an `sgv` and a `date` (ms)
   within 15 minutes of now; otherwise 422 whose detail is exactly one of
   `feed unreachable` (connection or timeout, a non-2xx other than 401/403,
   or a body that is not a JSON list), `feed refused the token` (401/403),
   `no reading in the last 15 minutes` (an empty list, a first entry with no
   `sgv` or `date`, or a `date` older than 15 minutes). The value read is
   discarded: never stored, logged, or returned (the test asserts no log line
   or response carries it). The relay is not told on a 422.
2. Relay `POST {RELAY_URL}/v0/users/{user_id}/verified` with `X-Cloud-Key:
   RELAY_CLOUD_KEY` and `Authorization: Bearer <user_bearer>`, body `{}`:
   relay 401/404 -> 404 `no such account`; unreachable, 5xx, or anything else
   -> 502.
3. Upsert `phone_accounts` (URL and token encrypted; a fresh dashboard token,
   `secrets.token_urlsafe(32)`, only its SHA-256 stored, replacing the old
   hash so an earlier token for that user stops working; 503 when Tiger is
   unreachable) -> `{user_id, verified: true, dashboard_token}`. The token is
   returned once.

`GET /v1/dash/{name}` accepts three credentials, each resolved to the device
it draws (`resolve_dash_device` in main.py), checked in this order:

| credential | draws | notes |
|---|---|---|
| `Authorization: Bearer <OWNER_BEARER>` | `DEVICE_ID` | the admin backup, unchanged; 503 while DEVICE_ID is unset |
| `Bearer <owner pairing token>` + header `X-Device-Id: <device_id>` | that device_id | the cloud asks the relay's `POST /v0/device/pair/check` (`X-Cloud-Key`) `{device_id, token}`; the answer is cached 5 minutes per sha256(token), ok and not-ok alike (a cached ok counts only for the device it was given for); not ok -> 401; ok but device_id != DEVICE_ID -> 404; relay unreachable -> 502 |
| `Bearer <a phone dashboard_token>` | `ns-<user_id>` | no rows until Phase 2 stores the feed, so every chart answers `empty: true` honestly |

Anything else is 401 as today, except that with OWNER_BEARER unset an
unrecognised bearer keeps today's fail-closed 503 (the phone-token and
pairing-token paths work without OWNER_BEARER). `demo=true` keeps its
meaning: `<device>-demo`. `/v1/family/last_night` and the other routes are
unchanged.

## Migrations

`python migrate.py` applies `sql/*.sql` in order, once, idempotently
(`schema_migrations`). The compose file runs it at boot.
