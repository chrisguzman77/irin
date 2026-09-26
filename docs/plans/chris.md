# Chris — Backend plan (docs/plans/chris.md)

Read this at the start of any session working backend/ or relay/.
CLAUDE.md has the repo rules; this file is WHAT to build and in what
order. The three spec documents (docs/) are the human reference; prefer
this file, contracts.py, relay/README.md, and the journal for working
context.

## Lane

Owns backend/ (app/, sounds/, tests/, including app/rounds/,
app/buddy/, forward.py, and voice_out.py), relay/ (the blind courier:
cards, doctor messages, buddy alerts, the WhatsApp channel (notify.py),
hub, and the buddy directory; documents on MongoDB Atlas; never a
glucose value), and cloud/ (Irin
Cloud: ingest, dashboards API, family rollup, audio; George owns
cloud/sql/), plus all shared files as owner of record (repo root,
.claude/, .cursor/rules, AGENTS.md, contracts.py, demo/, docs/, deploy/
including docker-compose.yml and the Caddyfile). Consumes
ml/models/predict.py and ml/models/nights.py (George) and hardware/hal.py
(Slavik); never edits any of them. Serves ../frontend/display at / as
a static dir with CORS for APP_ORIGIN and Vite's http://localhost:5173;
the hosted app (Justin's Vite + React build, committed as
frontend/app/dist/) and the role pages are served by the compose file's
Caddy on Vultr, and only the Device tab ever talks to the Pi (through
the tunnel, with the pairing token and the PIN). Every mutating route
and every WebSocket message shape lives in contracts.py, and the app's
TypeScript types are generated from the backend's /openapi.json (npm run
types), so a contract change is: edit contracts.py, tell Justin to
regenerate.

## Tiers and gates (binding)

Steps 1-12 are tier 1 (core Irin). I-steps are hour-one infrastructure
(domain, Vultr, Atlas, Tiger, keys) and C-steps are Irin Cloud (the
forwarder, ingest, aggregates, dashboards), both in the section after
Family Story; C2-C4 run in George's and Justin's lanes in parallel with
Rounds. F-steps are Family Story (right after core, about half a day).
R-steps are Rounds, in the build order of the
updated Rounds spec (docs/Irin_Rounds.pdf, 2026-09-25 version, which
supersedes the earlier two-card brief): the platform first, then Program
A (Standing Cards: Basal Check, Hypo Response, Follow-up), then Program B
(Step Watch), then recall, resources, and polish. B-steps are the Night
Buddy demo tier. Gates, from CLAUDE.md "Build sequencing": no F- or
R-step before step 12's check passes and The Save plays end to end; no
B-step unless R1-R11 (the minimum credible Rounds demo: one Standing
Card and one Step Watch card on the same patient, a real doctor action,
a real patient confirm, and the recall questions) landed with a full day
remaining before judging, otherwise Night Buddy is video-only. Each step
names its check; a step is done when the check passes. Rounds cut order,
first cut first, hold the line: night replay, step-week vigilance, Brain
versus Bedside side by side, the Follow-up card, real crypto (keep the
panel with an honest label), QR pairing (pre-pair instead), Basal
Check's "possibly too high" direction. Never cut: the confirm step, the
DEMO badges, the "no patient data" banner, the noise budget.

## Build order — tier 1, core (each step names its check)

1. **contracts.py** (day one, before anything). Models: Reading
   (timestamp, glucose mg/dL, trend, source, is_stale), Treatment,
   Forecast, AlarmState (state: idle/pending/active/acknowledged/rearmed,
   trigger_type distinguishing predicted_low vs actual_low vs high vs
   stale — the frontends render amber vs red off this field, so it is
   day-one, not a retrofit), Settings, PresenceState. WS message types:
   reading update, forecast update, alarm state change, acknowledge
   command, settings change, treatment logged, presence change, mode
   change. Plus state_snapshot: the ws hub sends a FULL state snapshot (latest
   reading, forecast, alarm state, settings, presence, mode) on every
   new socket connection, so clients render from snapshot + updates and
   never from assumed history. The Rounds, Night Buddy, and Family Story
   models listed under "Contracts additions" below ship in this same
   day-one file (decision B11: /skeleton had not run when the sponsor
   specs were adopted), so no later tier needs a contracts commit unless
   something is found missing, and then it is ONE announced commit.
   Check: mypy/pydantic validates; Justin and George confirm shapes.
2. **Replay datasource + clock.py.** Replay engine plays a scenario CSV
   at a speed multiplier behind the same interface as the Nightscout
   poller. ALL timers everywhere read clock.py, never time.time(), so
   replay accelerates alarms too. Check: a CSV plays at 60x and a
   15-minute re-arm fires in 15 seconds of wall time.
3. **Nightscout poller** (60 s), SQLite cache, stale marking (no reading
   for 15+ min = stale). Staleness is measured on clock.py's MONOTONIC
   elapsed time since the last NEW reading arrived, never as a wall-clock
   difference against the reading's timestamp: the Pi has no RTC
   battery, so its wall clock is wrong after any boot without network
   and would mark fresh data stale (or stale data fresh). Check: kill
   the network, display shows stale; set the wall clock a day off in a
   test, staleness is unaffected.
4. **Alarm engine (alarm.py)** — the tiered state machine below. Expose
   ONE observer hook, `on_transition(callback)`, that hands every state
   transition (old state, new state, trigger_type, reading, clock time)
   to registered observers. The AlarmEvent recorder (R2) and the buddy
   rung (B2) hang off this hook, so no later tier ever edits alarm.py.
   Sound assets: backend/sounds/make_tones.py synthesizes the four files
   the driver plays by name (alarm_soft: soft two-tone; alarm_urgent:
   louder, faster; chirp: one short chime; buddy_chime: a distinct
   three-note pattern) with numpy and the wave module; the skeleton runs
   it once and the small wavs are committed. Check: the named tier test
   plus a walk of every transition in tests.
5. **IOB (iob.py):** decay curve over configurable duration, default 4 h,
   reaches exactly zero. Check: unit tests incl. negative/absurd inputs.
6. **Forecast wrapper (forecast.py):** calls George's predict.py on every
   new reading; suspended when the last hour has gaps (never forecast on
   stale/gapped data). Check: gap in replay suspends forecasting and UI
   says so.
7. **Voice parser (voice.py):** parses "log 45 carbs and 5 units"; any
   insulin value is echoed back and requires explicit confirm before
   storing; partial parse asks for the missing piece; 10 s timeout
   discards. Parsed text never reaches shell/eval/SQL strings.
   Check: malformed input cannot store a number the user never confirmed.
8. **auth.py PIN gate** on EVERY mutating endpoint: acknowledge, settings,
   logging, demo panel, and later the check-in, pairing, revoke, message
   confirm, treating, and opt-in endpoints. The Cloudflare tunnel makes
   these public. One auth path only: the PIN header. No localhost
   exemption (cloudflared also connects from localhost). Two dependencies:
   require_pin (the PIN header) and require_fresh_pin (the same check,
   plus the rule that the PIN for this call must come from a fresh
   keypad or prompt entry, never from storage). Which endpoints are
   fresh-PIN is declared ONCE, as FRESH_PIN_ENDPOINTS in contracts.py, so
   backend and both frontends implement it from the same list: pairing
   confirm and doctor-message confirm (and any later high-stakes display
   verb). The kiosk display stays glanceable: all morning questions live
   in the PWA. Its one LOW-stakes verb is the big on-screen acknowledge
   during an alarm (a phone across the room at 2 AM is exactly the failure
   the device exists to prevent), which uses a PIN entered once on a touch
   keypad and kept in localStorage on the Pi's Chromium, and records
   ack_source = device on the AlarmEvent (the PWA's ack records phone).
   For fresh-PIN verbs the kiosk re-prompts the keypad every time, cached
   PIN or not. The PWA keeps its PIN in sessionStorage (justin.md rule 2);
   localStorage is a kiosk-only concession. Check: unauthenticated POSTs
   are rejected on every mutating route; a request to a fresh-PIN endpoint
   from the kiosk after a keypad entry succeeds, and display.js has no
   code path that sends a stored PIN to one (audit grep).
9. **Presence state machine (app/presence.py):** consumes raw
   presence/no-presence from hal (Slavik's reader or the mock). Home on
   any detection, instantly. Away only on ~15 min sustained absence (timed
   on clock.py, so demo replay speed shortens it to seconds) AND
   outside the night window; radar absence alone NEVER sets Away at
   night. The manual toggle (PIN-gated endpoint) always wins over every
   automatic input. Output gating happens in the hal layer (LEDs,
   speaker, display brightness only); alarm logic, forecasting, logging,
   and the phone path never check presence. Returning re-enables outputs
   immediately, so a still-active alarm sounds the moment presence
   returns. Because the night rule pins the STATE to Home overnight, the
   state is useless as evidence that a person was in the room at 3 AM;
   Rounds and Night Buddy therefore read the RAW radar value from hal
   (see R2), never this state machine. A raw sample of None (mock not
   driven, hal error) is no evidence either way and never counts toward
   Away. Check: tests/test_presence.py drives the mock through day/night
   transitions and asserts the night rule and toggle override.
10. **Scheduler:** night window, basal nudge (visual escalates at 1 h,
    email at 90 min, daily reset), morning report trigger, and from Rounds
    the ledger build at night-window end and program evaluation five
    minutes later (R3, R8). Clock guard: there is no RTC battery, so on boot the
    wall clock is untrusted until NTP syncs. The scheduler polls
    `timedatectl show -p NTPSynchronized --value` (every 10 s of clock
    time) and holds every wall-clock feature (night window, basal nudge,
    morning report, ledger, evaluation) until it reads yes; until then
    state_snapshot carries clock_synced = false and the display and PWA
    show a "clock not set" badge. Alarms, stale marking, and re-arm
    timers do not wait (they are relative, on clock.py). Under
    IRIN_HW=mock and in replay the guard is bypassed (clock_synced =
    true). The demo rule is simpler still: hotspot up before the Pi.
    Check: test with the guard reporting false asserts no scheduler job
    fires and the snapshot flag is false; true releases them.
11. **Morning report:** overnight stats, matplotlib PNG, a narrative
    summary (the direct Claude call until R13, then narrative.py's
    chain; two short paragraphs, plain language, no medical advice),
    email + stored report + morning screen. Pre-generate a fallback
    report for the demo (no-network path). In Rounds the generation call
    moves behind narrative.py (R13) and gains the no-invented-numbers
    validator; the fallback stays. Family Story rides this job (F-steps
    below, scheduled right after core).
12. **Demo panel + mode switch** (the app's Device tab, demo section): all PIN-gated. POST
    /api/mode swaps the active datasource (nightscout | replay) at
    runtime — both sit behind the same interface, so the swap is
    changing one reference. Switch semantics: reset alarm state to
    Idle, reset the clock multiplier, broadcast a mode_change WS
    message; on return to live, serve the cached real reading marked
    stale until a fresh poll lands. While replay is active every
    screen shows a DEMO badge (invariant 1). The other panel endpoints
    (scenario select, speed, pause feed, inject low, basal-time, and the
    sponsor controls: send card Bedside | Brain-only, brain_only toggle,
    simulate Spark offer, start watch, jump to step N day D, trigger
    buddy rung) work
    only in demo mode and 404 in live mode, so nothing can inject into
    live data. Injections are an overlay; the scenario CSV replays
    clean. Treatments logged while in demo mode are stored locally but
    NEVER posted to the real Nightscout, so judge-entered carbs stay
    out of Chris's actual medical record. Check: switch live→demo→live in
    tests and assert alarm reset, badge state, and that inject-low 404s in
    live mode.

## The alarm state machine (authoritative summary)

Two tiers. WARNING = predicted low (amber, softer distinct tone, patient
ramp). FULL = actual low (red, loud). Intensity carries information:
warning means ~30 minutes, full means none.

- Idle → Pending: N = 2 consecutive forecasts below the low threshold.
- Pending → Active: actual value crosses threshold, OR 5 min
  unacknowledged, whichever first.
- Pending → Idle: warning acknowledged (final for the episode, no re-arm),
  or forecast back above threshold for 2 consecutive readings.
- Episode: ends after those 2 recovered readings; a later fresh crossing
  is a NEW episode and warns again.
- Active → Acknowledged: user ack. Acknowledged → Re-armed if still below
  threshold 15 min later; Re-armed behaves as Active; repeats every 15
  min until back above threshold → Idle.
- Acknowledging a WARNING never suppresses or pre-acknowledges the actual
  low. Separate alarms, not one alarm at two volumes.
- Priority: actual low > predicted low > stale > high. Higher priority
  replaces lower immediately and does NOT inherit its acknowledgment.
- Stale during a low alarm: low keeps sounding, stale banner added.
- Highs: ONE-SHOT (single chime, amber tint, persistent indicator until
  recrossing). No ack, no re-arm. Optional off-by-default setting:
  remind if still above X after N hours. Highs respect quiet hours;
  ONLY lows override quiet hours.
- Presence Away gates room outputs only (in the hal layer); alarm logic
  here never checks presence.
- Rounds and Night Buddy OBSERVE this machine through on_transition and
  never modify it. The only sanctioned reach into its threshold path is
  R14(a) step-week vigilance, which can only make the predicted-low
  threshold MORE cautious for 7 days after a step-up and never touches
  the actual-low alarm.

**The named tier test (backend/tests/test_alarm.py):** in replay,
acknowledge a predicted-low warning, let the scenario cross the actual
threshold anyway, assert the full alarm fires.

## Build order — Rounds (R-steps; the platform, then Standing Cards, then Step Watch)

Source: the updated Rounds spec (docs/Irin_Rounds.pdf, 2026-09-25). Two
programs on one engine. Standing Cards (Basal Check, Hypo Response,
Follow-up) watch an ordinary patient month after month; Step Watch
watches the weeks after a GLP-1, tirzepatide, or weekly basal insulin
start until the dose settles. Both share the night ledger, the metrics in
ml/models/nights.py, the SignalCard model, the noise budget, the
encrypted delivery path, and the inbox. All thresholds below are
starting values (config, not code), tuned by George against the real
history. Every card metric carries a confidence label, measured (the
device recorded it) / reported (the patient answered) / inferred (from
the curve); a card never shows a blank row, and brain_only changes
labels, never blanks a row.

R1. **Contracts fixture round-trip.** The models are already in
    contracts.py from the skeleton (see "Contracts additions"); this
    step proves them: write backend/tests/fixtures/signal_card_standing.json
    (a Basal Check card) and signal_card_step.json (a step-check card)
    from the SignalCard model, plus low_event.json and
    low_event_recall.json, and hand them to Justin. Anything missing is
    ONE announced commit. Check: pydantic validates every fixture; Justin
    confirms both card layouts render from them.
R2. **AlarmEvent recorder (app/rounds/alarm_events.py).** An observer on
    alarm.py's on_transition hook that writes one AlarmEvent per episode:
    tier (predicted_low | actual_low | stale | high, the same names as
    AlarmState.trigger_type), started_at, acknowledged_at, ack_source
    (device | app | None), escalated (any escalation before the first
    ack: Pending→Active by timeout or crossing, or the 5-minute
    louder/strobe step), rearm_count, crossed_actual (a predicted_low
    episode with crossed_actual = False is a near-miss), presence_during,
    is_demo. presence_during is the RAW radar, aggregated ONCE, here, by
    this rule (decision B7): sample hal.get_presence() every 30 s of clock
    time during the episode; "home" if a majority of samples show
    detection OR any detection falls within the 2 minutes around alarm
    start; "away" if zero detections across the episode; "unknown"
    otherwise (mock without a driven value, hal error, brain_only).
    Verdicts about the episode from the raw radar, never
    PresenceState.mode. Stored in store.py (alarm_events). The recorder
    observes only; it never calls into alarm.py. Check:
    tests/test_alarm_events.py replays The Save and asserts one event
    with escalated = True and crossed_actual = True; a warning-only run
    yields crossed_actual = False; flicker both ways (a brief dropout
    while present still reads home; a single blip outside the start
    window in an empty room still reads away); an undriven mock reads
    unknown; the named tier test still passes; the /audit grep (no
    presence check inside alarm.py) still passes.
R3. **Night ledger + night classification (app/rounds/ledger.py,
    nights_adapter.py).** At night-window end (default 22:00-07:00, on
    clock.py) write the NightRecord for the night just ended: coverage_pct
    (non-stale readings ÷ expected, expected = window hours × 12; a 9-hour
    window expects 108, and 0.85 × 108 = 91.8, so 92 readings or more is
    adequate), reason_codes and code_source, rise_mgdl (glucose at 07:00
    minus glucose at night start + 2 h, each a 15-minute median),
    dawn_rise_mgdl (03:00 to 07:00), low_point_mgdl (minimum of the
    15-minute rolling median), tbr_pct, minutes_below_70,
    near_miss_count (predicted_low events with crossed_actual false and no
    crossing within 60 min), level2_count (excursions under 54 confirmed
    by 2 readings), alarm_event_ids, is_demo. Every metric and every
    reason code comes from ml/models/nights.py (George: classify_night,
    night_metrics); the ledger never re-implements one. Reason codes,
    one or more per night: late_meal (carbs within 3 h before night start
    or during the night), late_correction (bolus within 3 h before night
    start), basal_late / basal_missed (basal logged more than 60 min after
    the usual time, or not logged), exercise (logged after 17:00),
    treated_low (a low alarm followed by a rise above about 60 mg/dL
    within 2 h), stale (coverage under 85%), away (the presence toggle
    set to Away), clean (none of the above). code_source is logged on a
    patient who logs; on a Brain-only patient late_meal is inferred (a
    rise faster than about 2 mg/dL/min in the first 2 h of the night),
    treated_low is inferred (a reading under 70 followed by a rise above
    60 within 2 h), stale is fully available, and basal_late, exercise,
    and away are unavailable and shown as "context unavailable", never
    assumed clean. Idempotent: rebuilding replaces the row by night_date.
    Check: tests/test_ledger.py — a record appears at the window end
    under 60x replay; each reason-code rule at its boundary (a snack 2 h
    59 min before night start is late_meal, 3 h 01 min is not; a basal 61
    min late is basal_late; 92 readings adequate, 91 stale); a rebuilt
    night is identical.
R4. **Low events (app/rounds/low_events.py).** Each nocturnal low (under
    70 inside the night window, confirmed by 2 consecutive readings; a new
    event needs 2 consecutive readings at or above 70 in between) becomes
    a LowEvent: started_at, nadir_mgdl, nadir_at, minutes_below_70,
    auc_below_70 (mg/dL-minutes), recovery_slope (mg/dL per minute over
    the 30 min after the nadir), carbs_logged_within_30min,
    inferred_unfelt (a run of at least 20 min under 70 whose recovery
    slope is under 1.0 mg/dL/min with no carbs logged within 30 min),
    alarm_event_id, is_demo. Check: tests/test_low_events.py — a treated
    low (carbs at nadir + 20 min) is never inferred_unfelt; a slow
    recovery with nothing logged is; slope 0.99 fires and 1.0 does not;
    a 19-minute run never does.
R5. **Crypto + pairing (app/rounds/crypto.py, pairing.py).** PyNaCl
    crypto_box (X25519 + XSalsa20-Poly1305, authenticated). The device
    keypair is generated on first boot into backend/keys/ (gitignored):
    device_private.key never leaves the Pi, device_public.key is what
    pairing shares. seal(payload_json, peer_pk) → {nonce, ciphertext};
    open(...) for doctor messages, which therefore authenticate their
    sender. Pairing: POST /api/pair/start (PIN) creates a 128-bit
    single-use token with a 10-minute expiry on clock.py, registers
    {token, device_pk, is_demo} with the relay, and returns the QR URL
    <INBOX_URL>/pair#token=…&device_pk=…&relay=… (everything after # never
    reaches a server, so neither Caddy nor the relay logs a token or a
    key). The browser generates the doctor keypair, keeps the private
    key in localStorage, and posts doctor_pk; the Pi polls, and both
    sides show code4: the first 4 bytes of sha256(device_pk ‖ doctor_pk ‖
    token) as a big-endian integer, mod 10000, zero-padded to four
    digits, which defeats a key swap by anyone including the relay. The
    patient confirms on the device (fresh PIN) → Pairing paired, the
    relay issues the doctor's bearer, pairing_state broadcast. Revoke
    from either side (device: PIN; inbox: bearer) deletes keys on both
    sides and shows "sharing ended". peer_kind is doctor | buddy; the
    buddy handshake is identical, the watcher page being the browser
    peer. There is no typed-code path for the doctor or buddy pairing at
    the hackathon (the 6-digit typed code belongs only to owner pairing,
    R5+): if the QR will not scan, the demo phone is pre-paired (cut
    order); production
    pairing is the Impiricus-signed doctor directory as trust anchor,
    invites from Ascend including telehealth short codes, and passkeys
    protecting private keys. Check: tests/test_crypto.py (round trip;
    tampered ciphertext rejected) and tests/test_pairing.py (expired and
    reused tokens rejected; not paired until device confirmation; revoke
    stops sends; demo pairings are is_demo).
R6. **Relay service (relay/).** Second FastAPI app, documents on MongoDB
    Atlas (relay/store.py), CIPHERTEXT ONLY, the routes under "Relay API
    v0" below. Device-originated calls
    carry the X-Source-Key header; inbox and watcher reads carry the
    bearer issued at pairing. is_demo travels in the envelope (outside
    the ciphertext) and the relay rejects a card whose is_demo does not
    match the pairing's. GET /v0/log is the "what Impiricus sees" view:
    sender and recipient IDs, timestamps, sizes, ciphertext prefixes,
    never a plaintext field. CORS allows the
    irin-out-of-sleep-at-hackgt.tech origins and localhost. Rate limits
    per source key. relay/README.md publishes the
    "Clinical Signal Card v0" JSON Schema (the SignalCard plaintext that
    gets sealed; one schema, two programs, the program field decides
    which sections the inbox renders) and the versioned API: it is
    platform credibility, saying any device maker could publish cards
    into this channel. Storage: MongoDB Atlas through relay/store.py
    (R6+ below), so a redeploy never loses a pairing. Hosting: the Vultr
    box from deploy/docker-compose.yml behind Caddy at
    api.irin-out-of-sleep-at-hackgt.tech (I2). Fallback if Vultr or the
    venue network is unreachable (this
    paragraph is the ONE statement of it; CLAUDE.md, /audit, and the
    spec's Layer 5 cite it), in DEMO_SCRIPT.md: the same compose file on
    Chris's laptop serving EVERYTHING on one origin, http://<laptop-ip>
    (Caddy on plain HTTP for the LAN): the app's dist, the role pages,
    the relay, the cloud, and the local mongo and timescaledb containers
    standing in for Atlas and Tiger. The Pi's APP_ORIGIN is set to
    http://<laptop-ip>; the app's dist is rebuilt with the VITE_* values
    pointing at http://<laptop-ip> (or Justin runs `npm run dev --
    --host` on the laptop with APP_ORIGIN set to that :5173 origin) and
    the role pages' config.js point at the laptop; judges' phones on the
    hotspot open http://<laptop-ip> for the app and every role page, so
    nothing is ever mixed content. Nothing works at http://<laptop-ip>
    unchanged. Check: relay/tests/test_relay.py — key gating, bearer
    gating, single-use pairing, is_demo mismatch rejected, /v0/log shows
    no plaintext; pytest from relay/ green against the local mongo; the
    laptop fallback rehearsed once at home.
R7. **Relay client + card assembly (app/rounds/relay_client.py,
    cards.py).** Outbound only, no new open port on the Pi: POST sealed
    cards; poll GET /v0/device/{device_id}/messages every 60 s live and
    5 s in demo, on clock.py. cards.py assembles a SignalCard: program,
    kind, status, flags, the confidence map (metric → measured | reported
    | inferred), source (irin_bedside | irin_brain), patient_pseudonym,
    plan_id and step_index for watch cards, period, headline from code,
    metrics, nights (per night: date, reason_code, code_source, rise,
    and, for the optional replay, the 5-minute readings and events),
    excluded_counts, tolerance_days, narrative (template until R13),
    allowed_actions, resource_categories (empty for off-label use),
    is_demo, generated_at; seals it to the paired key; broadcasts
    card_sent. card_id derives from (program, kind, period, plan_id,
    step_index) so re-evaluation never duplicates a card. Check: a
    fixture card round-trips seal → relay → open in a test; the inbox
    shell (Justin) lists it.
R8. **Standing Cards + noise budget (app/rounds/standing.py, noise.py).**
    Pure functions: evaluate(night_records, low_events, recalls,
    settings) → (status, flags, metrics, confidence), no I/O, so the same
    code runs over history for validation. Built in this order, each
    shippable alone. **Basal Check (detect):** over the last 14 nights,
    at least 5 clean nights, a median overnight rise on clean nights
    beyond ±30 mg/dL, and at least 70% of clean nights in the same
    direction; the "possibly too high" direction fires when near-misses
    reach 3 or more in 14 days; never from stale nights; the card lists
    every excluded night with its reason (worked example: 8 clean of 14,
    6 rising, 6 ÷ 8 = 75% ≥ 70%, median +42 > +30 → fires, 6 excluded
    nights listed). Actions: adjust basal, schedule visit, ask patient,
    dismiss. **Hypo Response (safety):** in 14 days, at least 2 escalated
    warnings, or at least 1 re-arm, or a median acknowledgment time above
    5 minutes (counted only on events with presence_during = home), or
    any nocturnal low reported unfelt; adds patient-reported glucagon
    status. Actions: message patient, schedule visit, access and
    resources, dismiss. **Follow-up (verify):** starts when the patient
    confirms a dose change (R9); compares the 14 nights before against
    days 1-7 and days 1-14 after, needing at least 3 clean nights on each
    side, otherwise the card says "not enough data yet". noise.py
    enforces the whole budget in ONE place: one Standing Card per type
    per patient per 14 days; green never interrupts (weekly digest); red
    bypasses everything but is deduplicated per event and capped at one
    per type per 12 hours; a Step Watch suspends Basal Check and absorbs
    Hypo Response into its red safety card; nothing at all from stale
    nights or insufficient windows; a patient's cards are never split
    across two programs on the same day; Standing Cards resume at
    graduation with the post-graduation nights as the next baseline.
    Evaluation runs at 07:05 on clock.py; red rules run on every new
    reading and every AlarmEvent update. Check: tests/test_standing.py at
    the boundaries (5 clean nights passes, 4 does not; +30 does not fire,
    +31 does; 70% same direction passes, 69% does not; 3 near-misses
    flips the too-high direction; Follow-up with 2 clean nights on a side
    says "not enough data yet") and tests/test_noise.py (second Basal
    Check inside 14 days suppressed; red inside the window still sent
    once; an active watch suppresses Basal Check and merges Hypo
    Response).
R9. **Doctor messages + patient confirm (app/rounds/messages.py).** An
    incoming DoctorMessage must open with the paired doctor's key, else
    it is rejected and logged; stored pending; doctor_message_received
    broadcast (display and PWA show the confirm takeover, the PWA
    mirroring the device). POST /api/rounds/messages/{id}/confirm |
    decline (fresh PIN on the kiosk). Kinds and what confirm applies:
    insulin_change (insulin basal | bolus, new_units, start_date; on
    confirm Settings.basal_units updates, a therapy_change Treatment is
    logged, and a Follow-up comparison starts; the confirm screen echoes
    the units out loud on the device, the same echo-and-confirm rule as
    voice logging), plan_create and plan_update (R10), proceed, hold_step
    (hold_weeks ∈ {2, 4, 8}: every later planned_start shifts by the
    hold; holds stack), note, schedule_request, end_watch, dismiss.
    Decline and 24-hour expiry (clock.py) apply nothing; silence changes
    nothing. The device posts the resolution word (confirmed | declined |
    expired, metadata only) so the inbox shows "Patient confirmed
    07:14"; doctor_message_resolved broadcast. Prove the whole path ONCE
    on a basal change before Step Watch starts: adjust basal in the inbox
    → confirm by voice on the device → receipt in the inbox → Follow-up
    scheduled. Check: tests/test_messages.py — never applies without
    confirm; decline and expiry apply nothing; a message from a
    non-paired key is rejected; silence changes nothing; a confirmed
    insulin_change writes exactly one therapy_change Treatment.
R10. **Step Watch (app/rounds/step_watch.py).** TitrationPlan
    (drug_class glp1 | gip_glp1 | weekly_basal, steps pre-filled from a
    small JSON of label schedules and editable in the inbox's plan setup
    form, started_at, on_insulin, WatchOptions: ketone_prompts and
    step_week_vigilance are False on the model, and the plan setup form
    pre-fills both True for T1D / insulin users (ketone_prompts for T1D,
    step_week_vigilance for insulin users), vigilance_offset_mgdl 10,
    slower escalation every 8 weeks; status
    pending_confirm → active → graduated | ended). Demo default
    tirzepatide 2.5 → 5 → 7.5 → 10 → 12.5 → 15 mg, 4-week steps, so 15 mg
    at week 20 and graduation at week 24. Trigger: the simulated Spark
    offer (POST /v0/spark/new_rx on the relay, demo-only, no patient
    identity) or a manual start in the inbox; the plan arrives as a
    plan_create DoctorMessage and is active only after the patient
    confirms. Windows: baseline = the 14 nights before the start with
    coverage ≥ 85%, needing 5 such nights, else "baseline thin"; early
    check on day 3 evaluating nights 1-3, labeled limited data; step
    check on day 7 after each step evaluating days 3-7; step gate 3 days
    before each planned step-up (proceed or hold); graduation after 4
    consecutive green weeks at the maintenance dose, with a final card
    that compares baseline to now and doubles as visit prep. Status,
    evaluated in order: RED at any time and any coverage on a level 2
    low, a re-armed low alarm, or a ketone-risk episode (≥ 200 mg/dL for
    ≥ 120 min) on a day logged can't eat, sent immediately, deduped per
    event, one red per type per 12 h; INSUFFICIENT under 70% window
    coverage (no conclusions; red still sent); AMBER lows on low-point
    shift ≤ −15 mg/dL, near-misses ≥ 2, or TBR > 4%; AMBER awareness on
    at least one nocturnal low reported unfelt or not remembered, or at
    least two inferred suspected unfelt lows with no answers; AMBER
    tolerance on rough or can't eat on at least 3 of 5 days (hold becomes
    the prominent action); AMBER highs on at least 2 ketone-risk
    episodes; GREEN otherwise, into the weekly digest. Rate limit: one
    step check and one step gate per step, red exempt. Doctor actions on
    a watch card: proceed, hold 2 / 4 / 8 weeks, adjust insulin with
    typed units and a start date, message, schedule visit, access and
    resources, end watch. Signals it adds: tolerance (SymptomCheck gi
    fine | rough | cant_eat, one tap a day in the PWA during a watch;
    missing is no data, never fine), adherence (weekly glp1_dose
    Treatments with dose_label against the plan, plus a dose-mismatch
    flag), nocturnal burden. Program interaction (noise.py): starting a
    watch suspends Basal Check; Hypo Response stays live but merges into
    the watch's red safety card; Follow-up is shared. The demo's amber
    card is the worked example: coverage 1,390 of 5 × 288 = 1,440
    expected, floor 0.70 × 1,440 = 1,008, 1,390 ÷ 1,440 = 96.5% passes;
    shift 76 − 98 = −22 fires; TBR 58 ÷ 1,440 = 4.03% fires; 2
    near-misses fires; 1 low answered don't remember fires; rough 3 of 5
    fires. Headline: "Step 2 (5 mg), days 3 to 7. Overnight low point
    down 22 mg/dL from baseline, 2 near-misses, time below range 4.0%.
    One low not remembered. Rough stomach 3 of 5 days." Check:
    tests/test_step_watch.py at every boundary (−15 fires, −14 does not;
    4.03% fires, 4.00% does not; 3 of 5 fires; 70% coverage passes, 69%
    is insufficient with red still sent; 2 ketone-risk episodes fires
    highs; a 4-week hold moves every later step by 28 days and two holds
    stack; no message means no change; the early check is labeled
    limited; four green weeks graduate; a watch suppresses Basal Check).
R11. **Morning recall + unfelt-low rate (app/rounds/recall.py).**
    Self-report is the clinical standard for hypoglycemia awareness
    (Gold and Clarke), asked once in clinic about a vague multi-month
    impression, and the two instruments disagree in nearly 60% of cases;
    Irin asks about one specific low the next morning with the curve
    attached. At night-window end create one LowEventRecall per LowEvent,
    capped at the two deepest per morning, and broadcast recall_due; the
    PWA shows one card per event ("At 2:47 AM you were 58 for about 25
    minutes. Do you remember that?") with four answers: felt_and_treated,
    woke_no_symptoms, dont_remember, was_awake. If
    carbs_logged_within_30min, the card pre-fills treated and asks only
    whether symptoms were felt (yes → felt_and_treated, no →
    woke_no_symptoms). Asked once, in the morning window; at noon
    (clock.py) anything unanswered stays answer = None and is reported as
    "no answer", never fine. POST /api/rounds/recall/{low_event_id}
    {answer} is PIN-gated from the PWA; the kiosk shows only the passive
    chip ("2 questions waiting in the app"); one morning notification,
    best effort. unfelt_low_rate = (woke_no_symptoms + dont_remember) ÷
    answered, no_answer never in the denominator and reported beside it:
    "4 nocturnal lows in 14 nights, 3 answered, 2 unfelt (67%), 1 no
    answer" (2 ÷ 3 = 0.667). Both programs read it: Hypo Response fires
    on any reported unfelt low; Step Watch's awareness amber needs one
    reported unfelt low or two inferred ones with no answers. A late
    answer before noon updates the record and re-runs evaluation
    (idempotent). Check: tests/test_recall.py — two events make two cards
    and three make two; carbs at nadir + 20 min pre-fills treated;
    nothing answered by noon reads no answer and never counts as felt;
    the rate divides by answered, not total; a late answer updates the
    record without duplicating a card.
R12. **Replay seek + idempotent catch-up + demo data.** A 24-week watch
    cannot be played: 24 × 7 × 24 = 4,032 hours, and 4,032 ÷ 60 = 67
    hours of wall time at 60x. The replay datasource gains seek(to): it
    bulk-loads readings up to the target timestamp, advances clock.py,
    and lets the engine's catch-up generate every card that should exist
    by then. Catch-up derives what should have run from plan dates, night
    dates, and stored card IDs, never from "jobs that fired", so it
    survives reboots and seeks. Demo panel: POST /api/demo/seek {step,
    day} ("jump to step N day D", demo-only, PIN) plus the tier controls
    (send card Bedside | Brain-only, brain_only toggle, simulate Spark
    offer, start watch, trigger buddy rung), all 404 live. Demo data,
    George's scenarios: Standing Cards run on Chris's REAL history, 2 to
    3 real basal increases exported as date-shifted windows 14 days
    either side (reason codes on history are inferred and labeled so;
    never present inferred history as logged); Step Watch runs on the
    SYNTHETIC titration scenario with its companion JSON (plan, symptom
    checks, injection logs, recall answers, alarm-event overlay); the
    Brain versus Bedside comparison needs logged context, so it uses the
    synthetic scenario or nights logged live at the event. Check:
    tests/test_seek.py — seeking forward twice creates no duplicate
    cards; a reboot (fresh engine over the same store) mid-window gives
    the same cards; the basal-change window yields a Basal Check and,
    after the confirmed change, a Follow-up; the titration scenario
    yields step 1 green and the step 2 amber worked example verbatim.
R13. **Resources handoff, the panel, the narrative validator
    (app/rounds/narrative.py).** ONE narrative module for every Irin
    narrative: the morning report (reports.py becomes a client), card
    narratives (clinician prompt: 2 to 3 sentences, plain language, no
    recommendations, no dose numbers), and later the buddy lines (B5) and
    Family Story (F2 lands the validator first if it comes earlier; R13
    adopts it). One narrative.py call per task with a fixed prompt (the
    chain in R13+: Backboard → Meta direct → direct Anthropic →
    template); the validator
    extracts every numeric token (integers, decimals, percentages, clock
    times) from the generated text and requires each to exist in the
    card's computed metrics (including allowed forms such as "6 of 8");
    on any miss, on error, or with no network a deterministic template
    renders instead. Resources: POST /v0/resources/request {doctor_id,
    category, brand} from the inbox after category → brand → the mock
    Ascend handoff with the banner "No patient data shared with any
    manufacturer"; categories: glucagon access, GI side-effect education,
    copay and savings, samples including the next pen strength, bridge
    supply, prior authorization and hub enrollment, ask an MSL; empty
    for off-label use. The "what Impiricus sees" panel reads GET /v0/log.
    DEMO_SCRIPT.md gains the Rounds run of show (the eight beats: pairing,
    detect, decide, verify, the therapy start, the amber card, the pharma
    moment, close), the demo-data notes, and the Q&A ammunition from the
    Rounds spec; the honesty line at the detect beat, verbatim and never
    unqualified: "glucose real; reason codes inferred and labeled;
    acknowledge, presence, and recall data are a labeled overlay". Check:
    tests/test_narrative.py feeds a text with an invented number and
    asserts the template is used; a matching text passes; grep
    DEMO_SCRIPT.md, metrics.md, and META_WRITEUP.md for "real" and find
    the qualifier next to every hit.
R14. **Polish, in this order, each shippable alone.** (a) Step-week
    vigilance (app/rounds/vigilance.py): for 7 days after each step-up,
    raise the predicted-low threshold by vigilance_offset_mgdl (70 → 80)
    through the settings path only; it never touches the actual-low
    alarm and expires on its own. It is the only Rounds code that
    reaches the alarm threshold path, deliberately last. (b) Night
    replay in the inbox: one night rendered from the card's own nights
    list like game film, about 20 s. (c) Brain versus Bedside side by
    side: the demo panel's send-card control runs the adapter twice
    (source irin_bedside and irin_brain) so both cards sit in the inbox;
    in brain_only the device rows change confidence label (reported or
    inferred) and never blank. Check: tests/test_vigilance.py (the
    threshold rises by 10 for exactly 7 days and the actual-low alarm is
    untouched); a replay plays from a fixture; the two cards differ only
    in labels.

## Build order — Night Buddy demo tier (B-steps; gate: R1-R11 landed with a full day left)

B1. **Contracts fixture round-trip.** BuddyLink, BuddyAlert, HubListing,
    HubClaim, EmergencyScript, the Settings fields, and the WS types are
    already in contracts.py from the skeleton; write the alert and listing
    fixtures for Justin's watcher page. Anything missing is ONE announced
    commit.
B2. **Buddy rung observer (app/buddy/rung.py) + emergency-script clock.**
    On the alarm.py hook, like the recorder. Fires when a FULL alarm
    (Active or Re-armed) has been unacknowledged for 10 minutes after the
    actual-low crossing (T+10, config, range 10-12) AND the recorder's
    running presence_during verdict for the episode is home
    (in-room-but-unresponsive; an empty room means the bathroom). On a
    laptop the mock radar is undriven (unknown), so the demo panel's
    "trigger buddy rung" control drives it to detected first. brain_only
    has no presence, so its condition is stricter:
    confirmed level 2 low, sustained 10 min, zero phone interaction, and
    the alert is labeled unconfirmed. On fire: seal a BuddyAlert to the
    buddy's key and POST it (kind = buddy_alert in the envelope); if
    hub_watchable, open a hub listing. Re-arm philosophy: after an ack
    that is still low 15 min later, the rung re-climbs. Resolution on CGM
    recovery (two consecutive readings above the low threshold) or ack.
    The T+20 emergency-script clock is an INDEPENDENT timer on clock.py:
    at T+20 the script action fires (demo tier: a stored event, a WS
    line, and a display banner "emergency contact alerted (simulated)")
    regardless of claims, calls, or statuses. In demo mode alerts and
    listings are is_demo and go only to demo pairings and the demo hub.
    Named test (tests/test_buddy.py): full alarm, presence home,
    unacknowledged through two escalation cycles → buddy alert fires;
    never fires when radar is absent; a claim on the listing leaves the
    T+20 timer untouched; alarm.py's state and the tier test are
    unchanged.
B3. **Hub endpoints on the relay (relay/hub.py).** POST /v0/hub/listing
    (device-originated, key-gated; first name, alarm state, elapsed
    minutes, confidence device_confirmed | unconfirmed, is_demo; any
    glucose, location, or phone field is rejected), GET /v0/hub/list
    (volunteer bearer; ordered by urgency then confidence; demo listings
    only to demo volunteers), POST /v0/hub/claim (exclusive lease, 3 min
    starting value; a second simultaneous tap gets 409 with the holder's
    expiry), GET /v0/hub/claim/{id}/script (the patient's script, only to
    the live claim-holder, only while the lease is live; in the demo tier
    it is stored on the relay encrypted at rest with the relay key,
    because a not-yet-known volunteer cannot hold a pre-shared key. The
    production answer, which goes in the Q&A sheet: at claim time the
    relay notifies the device, which is by definition awake during an
    episode, and the device encrypts the script to the claim-holder's
    public key on demand, so the script is end-to-end even to a volunteer
    unknown in advance), POST
    /v0/hub/call (brokered: demo tier triggers the patient device's chime
    through its poll, no telephony, no numbers exposed), POST
    /v0/hub/treating (clears the listing, 20-minute expiry; on expiry
    without recovery the listing returns at TOP urgency), POST
    /v0/hub/resolve (device-originated on recovery or ack), GET
    /v0/hub/audit (every claim: who, when, actions, outcome). Check:
    relay/tests/test_hub.py — lease conflict; reopen on expiry; treating
    clears then returns at top urgency; script hidden after the lease;
    forbidden fields rejected; demo never lands in the real hub.
B4. **Patient side: alerts, treating, opt-ins (app/buddy/treating.py).**
    Buddy pairing = rounds/pairing.py with peer_kind = buddy (the watcher
    page is the browser peer; the pre-matched demo accounts are one
    Pi-side patient and one browser-side watcher on a teammate's phone).
    Nobody needs the device to take part: a watcher needs only the
    watcher page and a CGM feed behind the account; a Brain-only PATIENT
    is this same backend running elsewhere with IRIN_HW=mock, brain_only,
    and DATASOURCE=nightscout on their own Nightscout (on the Vultr box
    in production, decision 27; it could be one more container in the
    compose file later, but it is NOT deployed for the hackathon:
    Brain-only is demonstrated from the Pi's adapter with
    IRIN_BRAIN_ONLY=1 or the demo-panel toggle, or at most as an
    optional second instance on a laptop), posting listings marked
    unconfirmed under the stricter rule. That is an optional second
    instance, not new code. Four opt-ins in Settings
    (have_buddy, be_watcher, hub_watchable, hub_volunteer; all off by
    default; PIN). POST /api/buddy/treating (PIN, from the PWA's one-press
    button) → relay treating → treating_set broadcast. The Pi's poll also
    picks up brokered "call" events and plays the distinct buddy chime
    through hal (a new sound name, never the alarm tones). Check: end to
    end at 60x on a laptop: unacknowledged low → alert on the watcher page
    → claim → call → device chimes → ack → listing resolved.
B5. **Narrative lines + demo wiring.** narrative.py gains the buddy
    prompts (task buddy_line): the morning "all quiet" line for both
    sides and the event close-out ("Sam's okay. Your call at 3:12 woke
    him; he treated and recovered."), validated like every other
    narrative, sealed and delivered through the relay. Demo-panel
    control "trigger buddy rung"
    (demo-only). DEMO_SCRIPT.md gains the 45-second Night Buddy beat.
    Check: the close-out renders on both screens after the B4 run.

## Build order — Family Story (F-steps; right after core, before Rounds)

The Meta entry's built half; spec: docs/FAMILY_STORY.md. About half a
day. Lives in backend/app/family_story.py, rides the morning-report job
(step 11) and the narrative module (narrative.py once R13 exists; before
that, the same validator function lands here first and R13 adopts it).

F1. **Recipients and consent (Settings).** family_recipients: list of
    {recipient_id, name, email, level (story_only | story_and_view),
    send_mode (automatic | approve_each), state (active | paused |
    revoked), first_story_approved (bool)}. Default: the first story to a
    new recipient requires approval, then automatic. PIN-gated
    add/edit/pause/revoke endpoints; revoke is instant. Check: a revoked
    or paused recipient is skipped by the send hook (test).
F2. **Family prompt + inverted validator.** A family prompt variant:
    2-3 sentences in family register (how the night went, whether
    anything happened, that the patient handled it), always framing the
    patient's agency, never alarm, never medical advice, optionally one
    conversation starter not about glucose. Honesty: a rough night told
    calmly but truthfully; a no-data night says "Irin didn't have data
    last night", never "all fine". Validator rule inverted per level:
    Level 1 stories must contain ZERO glucose values (mg/dL numbers
    banned, clock times allowed); Level 2 stories keep the ordinary
    no-invented-numbers rule; any violation falls back to the
    deterministic family template. Save celebrations are never part of
    the automatic story (a later patient-initiated "Save Shared" action).
    Check: tests/test_family_story.py — a Level 1 story with a smuggled
    glucose number falls back to the template; a no-data night never
    renders as "fine".
F3. **Send hook + morning chip.** On the morning-report job: build one
    FamilyStory per active recipient; approve_each (and every first
    story) waits for the patient's tap (family_story_pending broadcast;
    POST /api/family/stories/{id}/approve | skip, PIN); automatic sends
    through the existing SMTP path with an unsubscribe line; Level 2
    includes the family-view link. Each story is also rendered once by
    ElevenLabs in the device voice (voice_out.py, cached by text hash;
    VOICE_BACKEND=none skips it) as a 20-second mp3 attached to the
    email and playable from the morning screen (audio_url on
    FamilyStory); the audio comes from the validated text, so it never
    carries a number the text does not. Broadcast family_story_sent; the
    morning screen and PWA show "Sent to Mom" with one-tap pause per
    recipient. Demo mode: stories are badged DEMO and NEVER reach SMTP
    (invariant 19), rendered on the morning screen only, the clip
    rendered locally and played there (so the video shot is
    reproducible), never emailed. Check: a demo-mode story never reaches
    SMTP (test with a fake mailer); the chip lists exactly the
    recipients that were sent; the attached mp3 is the cached clip for
    the sent text.
F4. **Stretch: story card via the relay.** POST the story sealed to the
    relay for a Level 2 recipient with a bearer, for the card at the top
    of frontend/family/. Only after R6 exists; otherwise skip.
    Check: the card renders above the unchanged family view.

## Build order — Irin Cloud, hosting, and the sponsor integrations (C-steps and I-steps)

Decided 2026-09-25 evening (docs/OPEN_ITEMS.md decision 27). Every item
here replaces something the earlier plan had, so none of it adds a
product. The domain is irin-out-of-sleep-at-hackgt.tech (registered
Friday night, Active on Cloudflare).

**I-steps, infrastructure, hour one (Chris, before anyone writes code).**

I1. **Domain and DNS.** The .tech domain's nameservers point at a free
    Cloudflare account. Records: six A records, @
    (irin-out-of-sleep-at-hackgt.tech), api., cloud., doctor., watch.,
    family. → the Vultr IP, all "DNS only" (grey cloud, proxy off, so
    Caddy can issue its certificates); device. is not hand-made: the
    Pi's Cloudflare named tunnel creates the CNAME (cloudflared tunnel
    route dns). Check: `dig +short api.irin-out-of-sleep-at-hackgt.tech`
    and `dig +short cloud.irin-out-of-sleep-at-hackgt.tech` answer from
    a phone on cellular.
I2. **Vultr box.** One Ubuntu 24.04 VPS (2 vCPU / 4 GB or more), Docker
    and the compose plugin installed, ports 80 and 443 open, the repo's
    deploy/ copied over with a .env holding ATLAS_URI, TIGER_URI,
    RELAY_SOURCE_KEYS, RELAY_ADMIN_KEY, RELAY_KEY, DEVICE_ID,
    DEVICE_TOKEN (cloud's ingest checks them for the one hackathon
    device), ELEVENLABS_API_KEY, ELEVENLABS_VOICE_DEVICE,
    ELEVENLABS_VOICE_ALERT, BACKBOARD_API_KEY, NARRATIVE_ROUTING,
    ANTHROPIC_API_KEY (the relay's direct-Claude fallback link),
    META_MODEL_API_KEY, WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID, DOMAIN.
    (The box that exists: vx1-g-2c-8g-120s in Atlanta, 2 vCPU / 8 GB.)
    `docker compose up -d` brings up caddy (Caddyfile:
    irin-out-of-sleep-at-hackgt.tech → the mounted frontend/app/dist
    with try_files to index.html, the relay at
    api.irin-out-of-sleep-at-hackgt.tech, the cloud at
    cloud.irin-out-of-sleep-at-hackgt.tech, the role pages), relay,
    cloud. Check: https://api.irin-out-of-sleep-at-hackgt.tech/v0/health,
    https://cloud.irin-out-of-sleep-at-hackgt.tech/v1/health, and
    https://irin-out-of-sleep-at-hackgt.tech load with a
    real certificate before the skeleton commit. Redeploy after a
    frontend merge: Justin has committed a fresh dist/; on the box `git
    pull && docker compose up -d` (Caddy serves the new files at once;
    only relay/cloud code changes need `--build`).
I3. **MongoDB Atlas.** One M0 cluster, one database user, network access
    0.0.0.0/0 for the weekend. Two databases: `nightscout` (set
    MONGODB_URI on the Railway Nightscout service; it restarts empty and
    refills from Dexcom Share. Nightscout's Admin Tools access tokens
    live in the database, so either restore the old data with
    mongodump/mongorestore or recreate the `irin` read token in Admin
    Tools and update NIGHTSCOUT_TOKEN on the Pi) and `relay` (the
    collections in R6). The laptop compose runs a `mongo:7` container
    instead. Check: Nightscout's /api/v1/status.json is green again, the
    Pi's poller still gets readings with the token in its .env, and the
    relay's health reports its store.
I4. **Tiger Cloud.** One free TimescaleDB service with the Toolkit
    enabled; TIGER_URI in .env; George's cloud/sql/ applied by
    cloud/migrate.py on first boot (idempotent). The laptop compose runs
    `timescale/timescaledb-ha:pg17` instead. Check: `SELECT * FROM
    timescaledb_information.hypertables` lists readings, alarm_events,
    low_events, treatments.
I5. **Keys.** ElevenLabs (one API key, two voice ids), Backboard (an API
    key from app.backboard.io, the Anthropic model names from GET
    /api/models?provider=anthropic written into config, never hardcoded;
    check its provider list once: it carries OpenRouter and not Meta, so
    buddy_line and family_story route through openrouter / the Muse
    Spark name only if OpenRouter carries Muse Spark, else meta /
    muse-spark-1.3 direct), Anthropic (the direct fallback), and the
    Meta developer app (decision 29): META_MODEL_API_KEY for the Model
    API at https://api.meta.ai/v1, on the Pi AND on the server; the
    WhatsApp Cloud API test number, WHATSAPP_TOKEN and
    WHATSAPP_PHONE_NUMBER_ID on the server only, up to five registered
    recipients who each send "hi" to the number during setup (the
    24-hour window); optionally the app id as VITE_FB_APP_ID in
    frontend/app/.env.production if Facebook Login ships. All in the
    private note that becomes the team's .env; never in the repo.

**C-steps, Irin Cloud (cloud/; Chris, with George's SQL and Justin's charts).**

C1. **Forwarder + ingest.** backend/app/forward.py: every 5 minutes on
    clock.py, POST /v1/ingest {device_id, readings[], alarm_events[],
    low_events[], treatments[]} with everything new since the last
    acknowledged batch, outbound only, batched, never blocking the poller
    or an alarm; on failure it keeps the cursor and retries next tick (the
    Pi works with no cloud at all, invariant 21). Demo readings carry
    is_demo and land in a separate demo device_id so real dashboards never
    show replay data. cloud/ingest.py (FastAPI on port 8200): validates
    the device token against DEVICE_ID and DEVICE_TOKEN read from
    cloud's own environment (the server's .env; one hackathon device),
    upserts on (device_id, timestamp) so a retry never duplicates, writes
    to the hypertables with psycopg. Check:
    cloud/tests/test_ingest.py sends the same batch twice and counts one
    row per reading; the Pi's tests assert an unreachable cloud changes
    nothing about alarms or staleness.
C2. **Hypertables and continuous aggregates (George, cloud/sql/).** See
    george.md step 9: readings, alarm_events, low_events, treatments as
    hypertables; daily_stats, overnight_profile, nightly (22:00 origin),
    hourly_heatmap as continuous aggregates with refresh policies; a
    compression policy after 7 days; time_bucket_gapfill for sensor gaps.
    Chris's part: cloud/migrate.py applies the numbered SQL files once.
    Check: the agreement test (george.md) is green.
C3. **Dashboards API (cloud/dash.py).** GET /v1/dash/{nights | tir |
    profile | lows_heatmap | alarms | near_misses | basal | sensor |
    step_watch | buddy | under_the_hood}?days=, owner bearer, each a
    thin query over an aggregate returning JSON the chart draws directly;
    GET /v1/family/last_night with a family bearer (low, high, minutes
    under 70, current value, stale flag); POST and DELETE
    /v1/family/bearers (PIN, from the app's Family section). Check:
    cloud/tests/test_dash.py over a seeded night; a revoked family bearer
    gets 401.
C4. **My Irin tab (Justin, A-steps).** Eleven charts from C3.
C5. **History load.** ml/load_history.py (George) loads the cleaned 22
    months into readings under Chris's device_id once, from the laptop
    where ml/data/ lives, printing counts only. Check: the nights strip
    shows 22 months and the under-the-hood tile reports the compressed
    size.

**Owner pairing, audio, narrative backends, relay storage (in the existing steps).**

R5+. **Owner pairing (app/rounds/pairing.py; the DevicePairing model,
    separate from Pairing, whose peer_kind stays doctor | buddy).** The
    kiosk shows a QR and a 6-digit code (single use, 10 minutes) only
    while hal.get_presence() reads True, and the Pi registers it with
    the relay: POST /v0/device/pairings (X-Source-Key) {code, device_id,
    device_url, token, expires_at}, device_url being DEVICE_URL from the
    Pi's .env (https://device.<domain> on the Pi, http://localhost:8000
    on a laptop). The QR encodes the same code plus the app URL, so
    scanning equals typing. The app redeems it: POST /v0/device/pair
    {code, username} → {device_id, device_url, token}; the relay writes
    a DevicePairing document (kind owner) and marks the code used. The
    Pi learns the completion (username) on its next relay poll
    (relay_client.py, the same poll as doctor messages; no keypad, the
    PIN in the app's header is the consent) and stores DevicePairing
    locally; the app stores device_url and token and sends both, plus
    the PIN header, on every Device-tab call; CORS on the Pi allows
    https://irin-out-of-sleep-at-hackgt.tech and localhost. Unpair:
    DELETE /v0/device/pair with the token from the app, or from the
    kiosk settings on the Pi; both sides revoke on the next poll. Check:
    tests/test_pairing.py gains the owner path (no code while presence
    is False; a used code is refused; the token never appears in a URL;
    unpair stops listings and the Pi drops the pairing on its next
    poll).
R6+. **Relay on Atlas (relay/store.py).** pymongo against ATLAS_URI (the
    laptop compose's mongo when unset): collections users, buddy_links,
    matches, hub_listings, hub_claims, pairings, cards, messages,
    resolutions, audit; TTL indexes on hub_listings.claim_expires_at and
    treating_expires_at as a janitor only (the lease and treating expiry
    still run on clock.py so 60x replay works). Nothing in any collection
    is a glucose value; the emergency number is encrypted with the relay
    key before storage and never returned. Check: relay/tests/test_relay.py
    runs against the local mongo container; a document dump greps clean
    for mgdl.
B3+. **Buddy directory and matching (relay/directory.py).** POST /v0/users,
    GET /v0/users/search?username=, POST /v0/match, POST
    /v0/match/{id}/accept | decline. The match is one aggregation
    pipeline: $match opted-in watchers with language overlap and a
    timezone in the requester's set; $addFields hours_covered (the
    requester's 22:00-08:00 local sleep window converted to UTC,
    intersected with the candidate's availability), mirror (UTC offset
    difference between 10 and 14 hours), shared_languages; score =
    hours_covered + 2 × mirror + shared_languages; $sort, $limit 3.
    Declined candidates never return. The introduction line and the
    why-this-match line come from narrative.py (task match_explanation:
    Muse Spark through the Meta Model API, written from the score
    parts; first names only, never a glucose value) and are text beside
    the match; the scorer never reads them. Availability typed as free
    text is parsed by narrative.py (task availability_parse) into
    week-grid rows the user confirms before they are stored, and only
    the confirmed rows score. Check: relay/tests/test_directory.py — a
    candidate asleep during
    the requester's night scores zero; a mirror scores higher than a twin
    with the same hours; declined candidates never return.
B4+. **The voice alert (relay/audio + cloud/audio.py).** On the rung
    firing, the relay asks cloud POST /v1/audio/render {kind buddy_alert,
    text "Your buddy Chris is in trouble. His alarm has been
    unacknowledged for twelve minutes.", voice alert} once, caches by
    hash, and puts audio_url in the BuddyAlert; the watcher page loops it
    (Justin). Beside it, the second channel (decision 29): relay/notify.py
    (new) sends the registered buddy a WhatsApp text through the Cloud
    API, POST https://graph.facebook.com/<version>/{PHONE_NUMBER_ID}/messages
    with WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID (server only): "Irin:
    your buddy <first name> is in trouble. The alarm has been
    unacknowledged for N minutes. Open <watch URL>", then the same clip
    as an audio message (type audio, link on cloud.<domain>). Never a
    glucose value in the message (the relay invariant; /review and
    /audit grep notify.py and the buddy prompts for mgdl). Free-form
    messages go only inside WhatsApp's 24-hour window after the buddy
    last messaged the number (each buddy sends "hi" during setup and
    again before the demo); outside it, an approved template. The
    emergency script (task emergency_script, Muse-written from the
    patient's rough notes into a calm ordered script with JSON fields,
    validated) is rendered the same way for the claim-holder. Check: the
    same text renders once; a cache hit costs no API call; a demo run
    puts the text and the audio message on the buddy's phone and the
    payload greps clean.
11+. **Spoken echoes on the Pi (backend/app/voice_out.py).** ElevenLabs
    directly from the Pi with the device voice: the doctor-message confirm
    ("Dr. Patel: basal 22 units from tomorrow. Confirm?"), the
    voice-logging echo ("30 carbs and 4 units, save?"), and the
    predicted-low voice after the amber tone starts ("Predicted low. 74 and
    falling."), rendered to mp3, converted with ffmpeg to wav into
    backend/sounds/generated/ keyed by text hash, played through hal after
    the tone, never instead of it. VOICE_BACKEND=none falls back to the
    tones alone. The morning report gains audio_url (rendered on the Pi,
    served by the Pi) and Family Story attaches the mp3 to the email (F3).
    Check: the tier test still passes with voice on; a missing key never
    raises in the alarm path.
R13+. **Narrative backends (app/rounds/narrative.py; decision 29).**
    NARRATIVE_BACKEND = backboard | anthropic | template names the FIRST
    link to try. Backboard: backboard-sdk's BackboardClient(api_key); one
    assistant per NarrativeScope (kind patient / doctor / buddy / family,
    scope_id), created on first use and stored with its thread_id;
    send_message(prompt, thread_id=..., llm_provider, model_name,
    memory="Auto" for patient, doctor, and buddy scopes, "Readonly" for
    family, stream=False). Meta direct: the Meta Model API at
    https://api.meta.ai/v1 (OpenAI-SDK compatible: the `openai` package
    with base_url, key META_MODEL_API_KEY on the Pi and on the server;
    models muse-spark-1.3 for text and structured output,
    muse-voice-transcribe-1.0 optional later for voice logging,
    muse-image-1.0). NARRATIVE_ROUTING (JSON, task → [provider, model])
    has EIGHT tasks, and the keys are final: clinician_card and
    morning_report → anthropic / a Sonnet-class Claude; buddy_line and
    family_story → openrouter / the Muse Spark name if Backboard's
    OpenRouter provider carries it, else meta / muse-spark-1.3;
    match_explanation, emergency_script, availability_parse → meta /
    muse-spark-1.3 (always direct; structured output); second_opinion →
    openai / a small model (a different provider from the writer, JSON
    output answering {invented_number, advice}). Provider meta is
    selected per task by the table, never by NARRATIVE_BACKEND. What
    Muse writes in Irin Buddy: the introduction line and the
    why-this-match line (from the score parts; first names only, never a
    glucose value), the emergency script (rough notes → a calm ordered
    script, JSON fields), free-text availability parsed into the
    week-grid rows the user then confirms (the deterministic scorer uses
    the rows), and the Family Story; the matching score stays the Atlas
    aggregation, and an LLM never picks the buddy. Chain of FOUR links
    with a 20-second timeout: Backboard → Meta direct (when the routing
    row names provider meta, or as the fallback for openrouter rows
    while Backboard is down) → direct Anthropic → the deterministic
    template; the no-invented-numbers validator runs after every link,
    and a card ships the template if the second opinion says
    invented_number or advice. Muse prompts never carry a glucose value.
    Demo seeding: the scenario's past nights are added as memories (POST
    /assistants/{id}/memories) so the live morning report demonstrably
    remembers. Check: tests/test_narrative.py gains a backend stub that
    returns an invented number and asserts the template; a stub second
    opinion that says advice forces the template; a timeout falls
    through to the next link in order; a routing row naming meta reaches
    the Meta stub and never the scorer.

## Contracts additions (the exact list; all in the day-one skeleton)

The Rounds models follow Appendix A of the updated Rounds spec verbatim,
with two project-wide additions marked (†): is_demo on every record the
demo-badging invariant touches, and peer_kind on Pairing because Night
Buddy pairs through the same handshake.

- NightRecord: night_date, window_start, window_end, coverage_pct,
  reason_codes (list[str]), code_source (logged | inferred | unknown),
  rise_mgdl (float | None), dawn_rise_mgdl (float | None),
  low_point_mgdl (float | None), tbr_pct (float | None),
  minutes_below_70, near_miss_count, level2_count, alarm_event_ids
  (list[str]), is_demo.
- AlarmEvent: event_id, tier (predicted_low | actual_low | stale |
  high), started_at, acknowledged_at (datetime | None), ack_source
  (device | app | None), escalated (bool), rearm_count (int),
  crossed_actual (bool), presence_during (home | away | unknown, the raw
  radar verdict for the episode from R2, not PresenceState.mode),
  is_demo (†).
- LowEvent: low_event_id, night_date, started_at, nadir_mgdl, nadir_at,
  minutes_below_70, auc_below_70 (mg/dL-minutes), recovery_slope (mg/dL
  per min over the 30 min after the nadir), carbs_logged_within_30min
  (bool), inferred_unfelt (bool), alarm_event_id (str | None), is_demo
  (†).
- LowEventRecall: low_event_id, asked_at, answered_at (datetime | None),
  answer (felt_and_treated | woke_no_symptoms | dont_remember |
  was_awake | None; unanswered after the morning window is reported as
  "no answer", never fine), is_demo (†).
- SymptomCheck: date, gi (fine | rough | cant_eat), is_demo.
- TitrationStep: index (0 = starting dose), dose_label (display only),
  planned_start (date).
- WatchOptions: ketone_prompts (False), step_week_vigilance (False),
  vigilance_offset_mgdl (10.0; predicted-low 70 → 80).
- TitrationPlan: plan_id, drug_class (glp1 | gip_glp1 | weekly_basal),
  drug_label (display only), steps (list[TitrationStep]), started_at
  (date), on_insulin (bool), options (WatchOptions), status
  (pending_confirm | active | graduated | ended), is_demo.
- SignalCard: card_id, schema_version ("0"), program (standing |
  step_watch), kind (basal_check | hypo_response | follow_up |
  early_check | step_check | step_gate | safety | graduation |
  baseline_note), status (green | amber | red | insufficient), flags
  (list of lows | highs | awareness | tolerance | level2 | rearm |
  ketone_risk | baseline_thin | dose_mismatch | missed_injection |
  rise_high | rise_low), confidence (dict: metric → measured | reported
  | inferred), source (irin_bedside | irin_brain), patient_pseudonym,
  plan_id (str | None), step_index (int | None), period_start,
  period_end, headline, metrics (dict), nights (list[dict]: date,
  reason_code, code_source, rise, plus readings and events when the
  replay ships), excluded_counts (dict), tolerance_days (list[dict]),
  narrative, allowed_actions (list[str]), resource_categories
  (list[str]), is_demo, generated_at. Published as "Clinical Signal Card
  v0" in relay/README.md.
- Pairing: device_id, doctor_id (the peer's id), doctor_display_name,
  doctor_pk, status (pending | paired | revoked), confirmed_at (datetime
  | None), peer_kind (doctor | buddy) (†), is_demo (†).
- DoctorMessage: message_id, plan_id (str | None), kind (plan_create |
  plan_update | proceed | hold_step | insulin_change | note |
  schedule_request | end_watch | dismiss), hold_weeks (int | None),
  insulin (basal | bolus | None), new_units (float | None), start_date
  (date | None), plan (TitrationPlan | None), text (str | None),
  created_at, status (pending | confirmed | declined | expired).
- Night Buddy (B1): BuddyLink (link_id, peer_id, mode twin | mirror,
  state, first_name), BuddyAlert (alert_id, event_id, urgency, confidence
  device_confirmed | unconfirmed, created_at, status), HubListing
  (listing_id, first_name, status open | claimed | treating | resolved,
  confidence, elapsed_min, urgency, claim_expires_at, treating_expires_at,
  is_demo), HubClaim (claim_id, listing_id, volunteer_id, claimed_at,
  expires_at, actions, outcome), EmergencyScript (steps: list[str]).
- Family Story (F-steps): FamilyRecipient (recipient_id, name, email,
  level story_only | story_and_view, send_mode automatic | approve_each,
  state active | paused | revoked, first_story_approved), FamilyStory
  (story_id, night_date, recipient_id, level, text, status
  pending_approval | sent | skipped | failed | demo, sent_at, is_demo).
- Settings gains: basal_units (float | None), glucagon_on_hand (bool |
  None), glucagon_expiry (date | None), night_buddy ({have_buddy,
  be_watcher, hub_watchable, hub_volunteer}, all False),
  emergency_script (EmergencyScript | None), family_recipients
  (list[FamilyRecipient], empty). Treatment.kind gains glp1_dose and
  therapy_change, and Treatment gains dose_label (str | None).
- DevicePairing (owner pairing, R5+; its own model, never a
  Pairing.peer_kind): device_id, user_id, device_url, token, paired_at,
  state (pending | paired | revoked).
- UserProfile (relay directory): user_id, username, first_name, languages
  (list[str]), timezones (list of IANA names), availability (list of
  {weekday, start, end}, local), sleep_window ("22:00"-"08:00", locked),
  cgm_feed ({nightscout_url, token}; verified once, never streamed),
  emergency_contact_ciphertext, emergency_script (EmergencyScript),
  optins (the four), created_at. BuddyMatch: match_id, user_id,
  candidate_id, score, hours_covered, mirror (bool), shared_languages,
  status (offered | accepted | declined). FamilyBearer: bearer_id,
  recipient_id, token_hash, created_at, revoked_at.
- NarrativeScope: kind (patient | doctor | buddy | family), scope_id,
  backboard_assistant_id, thread_id. SecondOpinion: invented_number,
  advice (bools). BuddyAlert, FamilyStory, and the morning report gain
  audio_url (str | None).
- FRESH_PIN_ENDPOINTS: the one list of endpoints that require a fresh
  keypad or prompt entry (pairing confirm, doctor-message confirm and
  decline), read by auth.py, display.js, and the app's usePin hook
  (frontend/app/src).
- WS types gained: card_sent, doctor_message_received,
  doctor_message_resolved, recall_due (the pending LowEventRecalls),
  symptom_check_due (the stomach check), pairing_state, plan_state,
  buddy_alert, hub_update, treating_set, family_story_pending,
  family_story_sent. state_snapshot gains: active_plan,
  pending_doctor_messages, todays_checkin_status (pending recalls and
  the stomach check), pairing_state, buddy_state (link, open alert,
  treating status), family_story_status (this morning's stories:
  recipient, status), clock_synced (bool; the step 10 guard; frontends
  show "clock not set" while false).

## Relay API v0 (relay/README.md is authoritative; keep them identical)

- POST /v0/cards {recipient_id, sender_id, nonce, ciphertext, source,
  kind, program, is_demo} (X-Source-Key). GET
  /v0/inbox/{recipient_id}?since= (bearer).
- POST /v0/messages (doctor → device, sealed, bearer). GET
  /v0/device/{device_id}/messages (source key; the Pi polls). POST
  /v0/messages/{id}/resolution {status} (source key; metadata only).
- POST /v0/pair {token, device_pk, is_demo} (source key). GET
  /v0/pair/{token} (public; pairing state and device_pk). POST
  /v0/pair/{token}/complete {doctor_pk, doctor_display_name} (public,
  single use). POST /v0/pair/{token}/confirm (source key; issues the
  bearer). POST /v0/pair/{id}/revoke (source key or bearer).
- Owner pairing (R5+; DevicePairing, kind owner): POST
  /v0/device/pairings {code, device_id, device_url, token, expires_at}
  (source key; the Pi registers the kiosk's code, 10 minutes, single
  use). POST /v0/device/pair {code, username} → {device_id, device_url,
  token} (public; the app redeems, the relay marks the code used).
  DELETE /v0/device/pair (the pairing token from the app, or the source
  key from the Pi's kiosk settings; both sides revoke on the next
  poll). The Pi reads completions and revocations on its
  /v0/device/{device_id}/messages poll.
- POST /v0/spark/new_rx (demo-only, key-gated; no patient identity, just
  "a new start in your panel").
- POST /v0/resources/request {doctor_id, category, brand} (bearer): the
  only thing a pharma-side system would ever see; the schema has no
  patient field.
- GET /v0/log (the "what Impiricus sees" view). Hub endpoints: B3.
- Buddy directory (B3+): POST /v0/users (PIN), GET /v0/users/search
  (bearer), POST /v0/match, POST /v0/match/{id}/accept | decline (bearer).
- Irin Cloud (cloud/README.md is authoritative): POST /v1/ingest (device
  token), GET /v1/dash/{name}?days= (owner bearer), GET
  /v1/family/last_night (family bearer), POST | DELETE /v1/family/bearers
  (PIN), POST /v1/audio/render (relay key or owner bearer).

## Numbers (starting values; config, not code)

Night window 22:00-07:00; coverage adequate at ≥ 85% (≥ 92 of 108 for 9
h), a night under it is stale; reason codes: late_meal 3 h,
late_correction 3 h, basal_late 60 min, exercise after 17:00,
treated_low rise above ~60 within 2 h; inferred late_meal ~2 mg/dL/min
in the first 2 h. Overnight rise from night start + 2 h to 07:00, dawn
rise 03:00-07:00, all 15-minute medians; low point = min of the 15-min
rolling median. Basal Check: 14 nights, ≥ 5 clean, median rise beyond
±30, ≥ 70% same direction; too-high direction at ≥ 3 near-misses in 14
days. Hypo Response: ≥ 2 escalated warnings, or ≥ 1 re-arm, or median
ack > 5 min (home only), or any reported unfelt low. Follow-up: 14
nights before vs days 1-7 and 1-14 after, ≥ 3 clean nights each side.
Standing rate limit one per type per 14 days; red deduped per event, one
per type per 12 h. Low events: under 70 confirmed by 2 readings; inferred
unfelt = ≥ 20 min under 70, recovery slope < 1.0 mg/dL/min over 30 min,
no carbs within 30 min; recall: one card per event, at most 2 per
morning, carbs within 30 min pre-fill treated, unanswered at noon = no
answer; unfelt-low rate = unfelt ÷ answered. Near-miss: no crossing
within 60 min. Level 2 = under 54 confirmed by 2 readings. Ketone-risk
episode ≥ 200 for ≥ 120 min. Step Watch: baseline 14 nights ≥ 85%
coverage, ≥ 5 nights; early check nights 1-3; step window days 3-7;
gate 3 days before a step; insufficient < 70%; amber on shift ≤ −15,
near-misses ≥ 2, TBR > 4.0%, 1 reported unfelt or 2 inferred, rough or
can't eat 3 of 5, ≥ 2 ketone-risk episodes; holds 2/4/8 weeks;
graduation 4 green weeks; vigilance +10 mg/dL for 7 days. Tirzepatide
2.5 → 15 mg in 5 steps, 4 weeks each (15 mg at week 20, graduation week
24). Ledger at night-window end, evaluation +5 min. Relay poll 60 s live
/ 5 s demo. Pairing token 10 min, single use. Doctor message expiry 24 h.
Buddy rung T+10 (10-12); emergency script T+20; lease 3 min; treating 20
min. presence_during: 30 s samples, majority OR any detection within 2
min of alarm start → home; zero detections → away; else unknown. All
timed on clock.py.

## Pi deploy (deploy/, Chris; Slavik runs them at the Pi)

The Pi is aarch64; every laptop is something else, and last year a
locally installed model died on exactly that gap. Rules: every backend
dependency has a prebuilt aarch64 wheel (backend/requirements.txt
carries anthropic and matplotlib for the morning report as well as the
core list; no pandas, no scikit-learn on the Pi); the forecaster ships
as XGBoost JSON, never a pickle; Python 3.14 everywhere, the Pi through
uv, code kept 3.13-compatible; and the stack is proven on the Pi the day
the skeleton lands (clone, uv venv --python 3.14, the two requirements
files, pytest, boot the mock backend, open the display page from the
laptop). Every promotion of dev to main is git pull, pytest ON THE PI,
then restart.

deploy/install.sh does, in order: abort unless `uname -m` is aarch64;
install uv and `uv python install 3.14`; apt install liblgpio-dev swig
build-essential ffmpeg chromium; `uv venv --python 3.14` and `uv pip
install` both requirements files (the 3.13 escape hatch beside it);
enable SPI; add the kiosk
user to spi, gpio, audio, video; screen blanking off (`raspi-config
nonint do_blanking 1`); the backlight udev rule (group video, g+w on
/sys/class/backlight/*/brightness); install irin.service as a systemd
--user unit for the kiosk user and `loginctl enable-linger` it, so
alarm audio shares the session's PipeWire with Chromium (a root system
service gets "device busy" or silence). The uv step above is the only
install of the two requirements files (gpiozero + lgpio for GPIO17,
Pi5Neo for SPI; RPi.GPIO and rpi_ws281x do not work on Pi 5); there is
no separate pip install. install.sh installs kiosk.service but leaves
it DISABLED; it never enables it.

**Kiosk autostart is the last change before judging, made by Chris by
hand, after /audit's fix-now list is empty.** Last time an always-on
kiosk respawned over the terminal while a fix was in progress; the unit
below cannot do that. deploy/kiosk.sh is the launcher: it exits 0 if
/boot/firmware/NO_KIOSK exists, sleeps 20 s unless called with `--now`,
then execs `chromium --kiosk --noerrdialogs --disable-infobars
--disable-session-crashed-bubble http://localhost:8000/`. kiosk.service
is a --user unit with ExecStart=deploy/kiosk.sh, After=irin.service,
ConditionPathExists=!/boot/firmware/NO_KIOSK, Restart=no, and
StartLimitBurst=1: it starts at most once per boot and never respawns.
During development the display page is opened in a normal Chromium
window; `deploy/kiosk.sh --now` shows the real kiosk on demand, and
Alt+F4 closes it. Escape hatches once autostart is on, all documented in
pi_setup.md: (1) `sudo touch /boot/firmware/NO_KIOSK` from a tty
(Ctrl+Alt+F2), over SSH, or from any computer holding the SD card, then
reboot: no kiosk; `rm` it to restore; (2) the 20 s delay is enough to
Ctrl+Alt+F2 and `systemctl --user stop kiosk`; (3) SSH from the laptop
works underneath the kiosk at all times, and `systemctl --user disable
kiosk` turns autostart back off. pi_setup.md also repeats the no-RTC
rule: boot with the network up. Check, at enable time and never
earlier: cold boot with the hotspot up, the kiosk comes up lit after the
delay, the badge clears once timedatectl syncs, a demo-panel inject-low
sounds through the soundbar with nobody at a terminal; then touch
NO_KIOSK, reboot, confirm the desktop comes up with no Chromium, remove
the flag, reboot, kiosk again.

Python 3.14 on the Pi (decision 27): install uv (curl -LsSf
https://astral.sh/uv/install.sh | sh), `uv python install 3.14`, `uv venv
--python 3.14 .venv`, `sudo apt install liblgpio-dev swig build-essential
ffmpeg`, then `uv pip install -r backend/requirements.txt -r
hardware/requirements.txt`; install.sh does the same. The escape hatch if
lgpio will not build in fifteen minutes: `python3 -m venv
--system-site-packages` on the system 3.13 of Raspberry Pi OS Trixie
(Debian 13, 64-bit) with apt's python3-gpiozero, python3-lgpio,
python3-spidev (decision 19's venv, superseded by 27 as the default and
kept only as this hatch); the code is written 3.13-compatible so
nothing else changes. irin.service also starts forward.py's tick (inside
the backend process, on clock.py) and needs the Pi lines of
.env.example in the Pi's .env (see the file; never a partial list
here).

## Submission day (Chris)

Build the public mirror with deploy/public_mirror.sh: squash history,
exclude journal/ and AUDIT.md, trim ml/models/metrics.md to the headline
numbers said on stage, dry-run into a temp directory, grep it for .env,
keys, ml/data, raw timestamps, and any "real night" claim without the
qualifier, then push to the fresh public repo named in the Meta form.
The working repo stays private.

## Invariants that bind this lane

Stale shown stale + never forecast on; insulin never stored without
echo-and-confirm (the insulin_change doctor message included); low
alarms override quiet hours; every mutating endpoint behind the PIN;
clock.py only, never time.time() or sleep(). Rounds: no dose numbers on
cards, validator or template, no conclusion from stale nights or windows
under 70% (insufficient, red still sent), red bypasses limits, the noise
budget lives in noise.py alone (one per type per 14 days, one check and
one gate per step, greens to the digest, a watch suspends Basal Check
and absorbs Hypo Response, never two programs on one day), sealed before
leaving, is_demo everywhere and demo → demo pairings only, doctor
messages apply only on confirm and only from the paired key, silence
changes nothing, alarm.py never reads presence and is never edited by
later tiers except R14(a) vigilance (predicted-low threshold only, 7
days); fresh-PIN endpoints never accept a cached kiosk PIN; every card
metric carries a confidence label and brain_only never blanks a row; a
missing morning answer is "no answer" and never counts as felt or fine;
the inferred-unfelt rule never fires on a treated low. Night Buddy:
buddy and hub rungs additive only, T+20 clock independent, script only
to the live claim-holder, no glucose or contact data on the hub, demo
never creates real listings or calls. Family Story: Level 1 never
carries a glucose value, demo never reaches SMTP, paused or revoked
recipients receive nothing, a no-data night is never "fine". Irin Cloud:
the relay never holds a glucose value (the WhatsApp payload and the Muse
prompts included); the forwarder is outbound, batched, and never blocks
an alarm; dashboards never feed a card and the agreement test stays
green; every link of the narrative chain (Backboard, Meta, Claude,
template) sits behind the validator and the second opinion, and no LLM
ever picks a buddy; every spoken clip is additive to a tone; the owner
pairing token never travels in a URL query. Run pytest from
backend/ (and relay/ and cloud/ when they change) before every commit.
