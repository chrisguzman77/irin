# /audit — full-codebase audit for Irin

You are auditing the complete Irin codebase before hackathon judging:
backend/, relay/, cloud/, every frontend page, ml/models/, hardware/,
demo/, deploy/.
You are the reviewer, not the author: assume nothing works until the code
shows it does. Read broadly before judging; verify every claim against the
actual source, not the docs.

Work through the checklist below in order. For each item, record PASS,
FAIL, or CONCERN with file and line references. Items for a tier that was
never built (still stubs or absent) are recorded as N/A with one line
saying so, never PASS. When done, write the findings to
`AUDIT.md` at the repo root, ordered most severe first, in two sections:
"Fix before judging" and "Known limitations (mention if asked)". Do not fix
anything yourself unless the human asks; this run is diagnosis only.

## 1. Safety invariants (highest priority)

Core:

- Stale data: trace the path from a data-source failure to the display.
  Can a frozen reading ever render without a stale indicator? Is forecasting
  suspended when the last hour has gaps?
- Insulin logging: find every code path that stores a treatment containing
  insulin units (touch, voice, any API endpoint, the insulin_change
  doctor message). Does each one require an explicit confirm after
  echoing the parsed values? Can a malformed voice parse store a number the
  user never confirmed?
- Low alarms: verify quiet-hours/volume settings cannot silence a low alarm.
- Alarm tiers: verify acknowledging a predicted-low WARNING can never suppress
  or pre-acknowledge the actual-low alarm (separate alarms in the state
  machine, not one alarm at two volumes), that an unacknowledged warning
  escalates to the full tier on threshold crossing or after 5 minutes, and
  that the tier test exists and passes (ack warning in replay, cross the
  threshold, assert the full alarm fires).
- Re-arm and escalation: confirm the 15-minute re-arm and 5-minute escalation
  actually fire, and that they (and the presence Away timer, the ledger,
  pairing expiry, message expiry, lease, and treating timers) read time from
  clock.py, not time.time().
- Demo mode: whenever the replay source is active, every screen (display,
  PWA, inbox, watcher page) shows the DEMO badge; the injection and
  demo-panel endpoints (inject low, pause, speed, scenario, send card,
  Spark offer, start watch, jump to step, trigger buddy rung) return 404
  in live mode;
  switching modes resets alarm state and the clock multiplier; treatments
  logged in demo mode are never posted to the real Nightscout.
- Presence gating: confirm Away suppresses outputs only, in the hal layer
  (LEDs, speaker, display brightness) — grep for any presence check inside
  alarm.py, forecast.py, or the logging path; any hit is a FAIL. The only
  other sanctioned readers of presence are backend/app/presence.py (the
  state machine), the AlarmEvent recorder (raw radar → presence_during),
  and the buddy-rung observer (raw radar → fire or not); confirm they read
  hal's raw value, never the Home/Away state, and never write back into
  alarm state. Verify the manual toggle overrides the radar in both
  directions, and that radar absence alone can never produce Away during
  the night window.

Rounds (backend/app/rounds/, relay/, frontend/clinician/):

- Dose recommendations: grep every card template, prompt, headline
  builder, and allowed_actions list for units, mg, doses, or "increase /
  decrease"; any dose number a card could carry is a FAIL.
- Narrative validator: confirm every number in generated text is checked
  against the computed metrics, that a rejected narrative renders the
  deterministic template, that a network failure does too, and that the
  test with an invented number exists and passes. Confirm the morning
  report goes through the same path.
- Plan and dose changes: find every write to a TitrationPlan and to
  Settings.basal_units. Each must originate from a DoctorMessage in
  status confirmed; decline, expiry, and silence must apply nothing; the
  hold arithmetic shifts every later step and stacks; a confirmed
  insulin_change writes exactly one therapy_change Treatment and starts
  a Follow-up.
- Message authenticity: an incoming message that does not open with the
  paired doctor's key is rejected before storage; a message from a revoked
  pairing is rejected.
- Coverage, staleness, and clean nights: a night under 85% coverage is
  stale and never feeds a card; a step window under 70% yields
  insufficient and still sends red; stale-flagged readings do not count
  toward coverage; Basal Check reads clean nights only, needs 5 of them,
  and lists every excluded night with its reason; reason codes on
  history are marked inferred everywhere they render.
- Confidence labels: every metric on every card carries measured /
  reported / inferred; grep the inbox and the card builder for any
  "requires Irin Bedside" or blank-row rendering; in brain_only mode the
  device rows change label and never blank.
- Morning recall: one LowEventRecall per nocturnal low, at most two per
  morning, asked once in the morning window; anything unanswered stays
  "no answer" at noon and is never counted as felt or fine; carbs within
  30 minutes of the nadir pre-fill treated and nothing else is ever
  pre-selected; the unfelt-low rate divides by answered, never total;
  the inferred-unfelt rule (run ≥ 20 min, slope < 1.0, no carbs) never
  fires on a treated low. The recall endpoint is PIN-gated and the kiosk
  exposes no button for it.
- Noise budget: rate limits and program interaction live in noise.py
  and nowhere else; one Standing Card per type per 14 days; one step
  check and one step gate per step; greens go to the digest; red bypasses
  the digest and every limit but dedupes per event with the 12 h cap;
  an active watch suppresses Basal Check and merges Hypo Response into
  the watch's red card; no day carries cards from both programs;
  Standing Cards resume at graduation.
- Encryption: trace a card from cards.py to the relay POST; the body must
  be nonce + ciphertext plus envelope metadata only. Grep the relay for any
  plaintext card field in storage or logs; /v0/log must show IDs, sizes,
  timestamps, and ciphertext prefixes only.
- is_demo: propagated from mode into AlarmEvent, NightRecord, cards,
  pairings, listings; the relay rejects a card whose is_demo does not
  match its pairing; the inbox badges DEMO from the envelope.
- PIN: recall answers, the stomach check-in, pair/start, pair confirm,
  revoke, message confirm and decline, opt-ins, treating, family
  recipients, story approve/skip, and every demo-panel control sit
  behind auth.py; the kiosk display sends the header (no localhost
  exemption) and its only cached-PIN verb is the on-screen acknowledge,
  which records ack_source = device. Fresh PIN: every endpoint in
  FRESH_PIN_ENDPOINTS (pairing confirm, doctor-message confirm/decline)
  uses require_fresh_pin, and display.js has no code path that sends a
  localStorage PIN to one of them; the app (frontend/app/src: App.tsx
  and the usePin hook) keeps its PIN in sessionStorage only.
- alarm.py: diff it against the last core commit; any change made by a
  Rounds or Night Buddy branch is a FAIL unless it is the R14(a)
  vigilance step, which may only raise the predicted-low threshold by
  the configured offset for 7 days after a step-up and must expire on
  its own.

Night Buddy (backend/app/buddy/, relay/hub.py, frontend/watch/):

- The buddy rung and hub never delay, quiet, replace, or gate a local
  alarm or the T+20 emergency-script timer; the named test (full alarm,
  presence home, unacknowledged through two cycles → alert; never when
  absent; a claim leaves T+20 untouched) exists and passes.
- The patient's script is returned only to the live claim-holder and only
  while the lease is live; after expiry or resolution the endpoint denies.
- Listing and alert payloads (the WhatsApp message included) carry no
  glucose value, location, or phone number, and the relay rejects such
  fields; confidence is always set and the watcher page renders the two
  tiers distinctly.
- Opt-ins: four separate settings, all default off; revoking a pairing
  deletes keys on both sides; claims are written to the audit log.
- Treating: one endpoint, one press from the PWA notification action;
  expiry returns the listing at top urgency when glucose has not
  recovered.
- Demo: alerts and listings created in demo mode never reach a non-demo
  pairing or the real hub, and no telephony call can be placed from demo.

Family Story (backend/app/family_story.py, the family prompt, the
recipient UI):

- A Level 1 story can never contain a glucose value: the inverted
  validator rule exists, the test with a smuggled mg/dL number passes,
  and the fallback is the deterministic family template.
- A demo-mode story never reaches SMTP (test with a fake mailer); a
  paused or revoked recipient receives nothing; a no-data night never
  renders as "fine"; the first story to a new recipient waits for
  approval; every email carries an unsubscribe line.
- The "Sent to" chip reflects only stories the backend marked sent.

Irin Cloud, the hosted app, and the sponsor integrations (cloud/,
backend/app/forward.py, backend/app/voice_out.py, relay/store.py,
relay/directory.py, relay/notify.py, frontend/app/):

- The relay's collections hold no glucose value: dump every collection
  in the local mongo container after a full demo run and grep for mgdl,
  glucose, readings; any hit is a FAIL. Grep relay/notify.py (the
  WhatsApp payload builder) and the buddy prompts for mgdl the same way;
  any hit is a FAIL. Buddy profiles hold the CGM feed URL and token for
  the one-time check only, never a stream.
- Matching stays deterministic: the score is the aggregation in
  relay/directory.py over the profile rows the user confirmed; the
  Meta-routed tasks (match_explanation, emergency_script,
  availability_parse) produce text or rows the user confirms and never
  feed the scorer; no LLM call sits inside the match pipeline.
- The forwarder: outbound only, batched every 5 minutes on clock.py,
  cursor kept on failure, never awaited inside the poller or the alarm
  path; the test that an unreachable cloud changes nothing about alarms
  or staleness exists and passes; demo readings go to the demo
  device_id and never into the real dashboards.
- Ingest upserts on (device_id, timestamp): the same batch twice yields
  one row per reading; the device token is required.
- Dashboards never decide: grep backend/app/rounds/ and alarm.py for any
  import or HTTP call into cloud/ or Tiger; any hit is a FAIL.
  ml/tests/test_agreement.py exists and passes against TIGER_URI (a
  scratch schema on the Tiger Cloud service) or against the compose
  container when Chris runs it.
- Family bearers: one rollup and the current value, nothing else; a
  revoked bearer gets 401; no Nightscout token in any page source.
- The emergency number: encrypted before storage with the relay key,
  never returned by any endpoint or log; the instructions only to the
  live claim-holder.
- Narratives: every link's text passes the validator (the chain is
  Backboard → Meta direct → direct Anthropic → template, the validator
  after each link); the second opinion runs on card narratives with a
  different provider and disagreement ships the template (test with
  stubs); family scopes use memory Readonly; timeouts fall through the
  chain in that order; provider meta is selected per task by
  NARRATIVE_ROUTING (eight task keys), never by NARRATIVE_BACKEND; a
  missing key (Backboard, Meta, or Anthropic) never raises in the 07:00
  job.
- Voice: clips cached by text hash (no re-render on a cache hit); a
  clip plays after the tone, never instead of it; VOICE_BACKEND=none
  leaves every alarm path working; the tier test passes with voice on.
- Owner pairing: the code is shown only while presence reads True, is
  single use with a 10-minute expiry, never appears in a URL query
  (the Pi registers it with POST /v0/device/pairings, the app redeems
  it with POST /v0/device/pair {code, username}, and the relay marks it
  used, so a second redemption is refused); unpair (DELETE
  /v0/device/pair from the app, or the kiosk settings) revokes on both
  sides on the next poll; CORS on the Pi allows only the app's origins
  and localhost; the VITE_DEVICE_URL override is honored only while
  /api/health reports hw: mock.
- The app: the PIN lives in sessionStorage only; the Device tab sends
  the pairing token and the PIN on every mutating call; the sleep window
  22:00 to 08:00 cannot be unchecked in the availability grid.
- The compose file and Caddyfile hold no secrets (env only); the laptop
  fallback was rehearsed exactly as chris.md (R6 fallback) states it:
  the same compose file on Chris's laptop serving everything on one
  origin, http://<laptop-ip>, with the app's dist rebuilt for it and the
  Pi's APP_ORIGIN set to it. The pages do not work there unchanged.

## 2. Contracts consistency

- Every WebSocket message type sent or handled in display.js, the app's
  frontend/app/src (the useDeviceSocket hook), watch.js, and ws.py
  exists in contracts.py with matching fields,
  including card_sent, doctor_message_received, doctor_message_resolved,
  symptom_check_due, pairing_state, plan_state, buddy_alert, hub_update,
  treating_set, and the state_snapshot additions.
- predict.py's signature matches how forecast.py calls it; nights.py's
  signatures (classify_night, night_metrics, low_events,
  standing_window, step_window_metrics) match how ledger.py,
  low_events.py, standing.py, and step_watch.py call them, and George's
  validation scripts call the same functions.
- hal.py's interface matches every call from alarm.py, main.py, the
  recorder, and the buddy rung.
- relay/README.md matches the relay's actual routes and the JSON the
  inbox and watcher page send; the Clinical Signal Card v0 schema matches
  SignalCard in contracts.py, program field included.
- demo/scenarios/README.md matches what make_scenarios.py writes and what
  the replay seek and catch-up read (reason codes, plan, symptom checks,
  injection logs, recall answers, alarm-event overlay, the confirmed dose
  change).
- No module defines its own private copy of a shared model or metric.

## 3. Security

- auth.py's PIN gate covers EVERY state-changing endpoint on the Pi:
  acknowledge, settings writes, treatment logging, demo-panel injection,
  and every Rounds, Night Buddy, and Family Story route listed in section 1. List any unprotected
  mutating route (the Cloudflare tunnel makes these public).
- Relay: device-originated routes require the source key; inbox and
  watcher routes require the bearer issued at pairing; /v0/spark/new_rx
  is demo-only and key-gated; /v0/resources/request has no patient field;
  pairing tokens are single-use with a 10-minute expiry; CORS is limited
  to the known origins; per-source rate limits exist.
- No secrets in the repo: grep for API keys, the Nightscout secret, SMTP
  passwords, hardcoded PINs, relay source keys, Anthropic keys, the Meta
  Model API key, the WhatsApp token, and any private key material
  (backend/keys/, doctor keys). Confirm
  .env, backend/keys/, and every .db are gitignored and .env.example
  contains placeholders only. The repo is PUBLIC (no mirror): confirm no
  .env, keys, ml/data, or raw timestamps are tracked or anywhere in the git
  history, AUDIT.md is gitignored, and no doc makes an unqualified "built
  from real nights" claim.
- Nothing under ml/data/ is tracked by git; no real CGM timestamps in
  demo/scenarios/ (they must be date-shifted); synthetic scenarios are
  labeled SYNTHETIC in the CSV name, the companion JSON, and the README.
- Voice input path and every other text input (check-in answers, doctor
  message text, typed insulin units, plan setup fields, emergency
  script, hub payloads):
  confirm parsed text can never reach a shell, eval, or SQL string, and is
  never rendered unescaped in any page.

## 4. Correctness spot-checks

- IOB math in iob.py: decay reaches zero at the configured duration; doses
  sum correctly; negative or absurd inputs rejected.
- Alarm state machine: walk every transition in alarm.py; flag unreachable
  states or transitions that skip acknowledgment.
- Replay engine: speed multiplier applies to both data timing and alarm
  timers; "trigger low now" injection cannot corrupt the underlying scenario.
- Clock: staleness is measured as monotonic elapsed time since the last
  new reading, never as a wall-clock difference; the scheduler's
  NTP-synchronized guard exists, holds every wall-clock job while false,
  and clock_synced reaches the snapshot; setting the wall clock a day off
  in a test changes nothing about alarms or staleness (no RTC battery).
- presence.py uses gpiozero with the pull-down enabled; no RPi.GPIO or
  rpi_ws281x import anywhere under hardware/.
- Context rules: meal-suppression window, insulin-sensitivity window, and
  basal-flag logic match the spec's numbers (30 min, 2-3 h, 1 h).
- nights.py: reproduce the worked examples by hand (8 clean of 14 with
  6 rising → 6 ÷ 8 = 75% and median +42 fires Basal Check; 1,390 of
  1,440 = 96.5%; 76 − 98 = −22; 58 of 1,440 = 4.03%; 0.85 × 108 → 92
  readings; 4 nocturnal lows with 3 answered and 2 unfelt → 67%, 1 no
  answer) and confirm the tests do too.
- Rules at the boundaries: −15 fires and −14 does not; 4.03% fires and
  4.00% does not; 3 of 5 fires; 92 readings passes and 91 fails; +30 does
  not fire and +31 does; 70% same direction passes and 69% does not; 5
  clean nights passes and 4 does not; 3 near-misses flips Basal Check's
  too-high direction; 2 escalated warnings or 1 re-arm fires Hypo
  Response; median 5.0 min does not fire and 5.5 does; a recovery slope
  of 0.99 is inferred unfelt and 1.0 is not; three events yield two
  recall cards; a metric that cannot be computed is None, never zero, and
  renders with a label, never blank.
- AlarmEvent recorder: one event per episode; escalated, rearm_count, and
  crossed_actual match a hand-walk of The Save; presence_during sampled
  from raw hal every 30 s on clock.py and aggregated by the one rule
  (majority OR any detection within 2 min of alarm start → home; zero
  detections → away; else unknown), with the flicker tests both ways
  present and passing; a None sample never counts as absent anywhere,
  including presence.py; the recorder never mutates alarm state.
- Ledger and seek: the ledger builds at night-window end on clock.py;
  seeking forward twice produces identical rows and no duplicate cards;
  a fresh engine over the same store mid-window yields the same cards; a
  late morning answer updates the record without a second card; the
  basal-change scenario yields a Basal Check before the change and a
  Follow-up after the confirmed change; the titration scenario yields
  step 1 green and the step 2 amber worked example verbatim.
- Hub: a second simultaneous claim gets a clean conflict; an expired lease
  reopens the listing; treating clears then returns at top urgency after
  20 minutes without recovery; resolution is by glucose or ack, never by a
  volunteer's report.

## 5. Hygiene

- `pytest` passes from backend/, relay/, and cloud/ (cloud against the
  compose containers). Note any skipped or
  trivially-true tests, and any placeholder test for a tier that shipped.
- Dead code, unused endpoints, leftover debug prints, TODO/FIXME bombs.
- README: follow its run instructions literally in a clean checkout,
  including the relay and the static pages; note every step that fails or
  is missing.
- requirements.txt files (backend, relay, ml, hardware) install cleanly;
  no unpinned package that broke recently; xgboost pinned to one version
  across backend and ml; backend/requirements.txt carries anthropic and
  matplotlib and nothing without an aarch64 wheel (no pandas,
  scikit-learn, torch, or any local-LLM runtime); no *.pkl or *.joblib
  anywhere in the repo, the model is forecast_v1.json, and predict.py
  imports only numpy and xgboost. Python 3.14 in every venv (or the
  documented 3.13 escape hatch on the Pi, with the journal saying so),
  and no 3.14-only syntax anywhere (t-strings, 3.14-only stdlib). If the
  Pi is reachable, run pytest on it: a green laptop is not a green Pi.

## 6. Demo readiness

- Every scenario CSV in demo/scenarios/ loads and plays without errors at
  60x speed, and a seek to any step of every companion JSON builds its
  cards without errors.
- The demo panel's basal-time button works and resets.
- The morning report generates with the fallback (no network) path; a
  card narrative does too.
- Rounds, detect / decide / verify: pairing works by QR on every team
  phone (record which phones were tested) and the demo phone is
  pre-paired as the fallback; the Basal Check card lands from the
  basal-change scenario with its excluded nights listed; adjust basal
  round-trips to a device confirm by voice and "Patient confirmed" in the
  inbox; the seek forward yields the Follow-up card; the "what Impiricus
  sees" panel shows ciphertext only; the Bedside and Brain-only cards
  can be shown side by side if R14(c) shipped. DEMO_SCRIPT.md carries the
  qualifier at the detect beat ("glucose real; reason codes inferred and
  labeled; acknowledge, presence, and recall data are a labeled
  overlay") and its Q&A sheet carries the emergency-script production
  answer; grep the repo for "real night" and fail any unqualified claim.
- Family Story: a replayed night produces the morning chip ("Sent to
  ...") without any SMTP call in demo mode, and the video's morning shot
  can be reproduced on demand.
- Rounds, the therapy start: the Spark offer appears, the plan setup
  form's Accept reaches the device confirm, "jump to step 2 day 7" yields
  the amber card reading the worked example (including "One low not
  remembered"), Hold 4 weeks and adjust insulin both round-trip to
  "Patient confirmed", the resources handoff shows the "No patient data
  shared with any manufacturer" banner, and the Hypo Response row's
  glucagon status drives the resources category.
- Night Buddy (if built): the 45-second beat runs end to end on a
  teammate's phone with DEMO badges everywhere.
- Connectivity fallbacks: irin-out-of-sleep-at-hackgt.tech and its
  api., cloud., device., doctor., watch., and family. hosts all load
  over HTTPS from a phone on cellular and on the hotspot
  (https://api.irin-out-of-sleep-at-hackgt.tech/v0/health and
  https://cloud.irin-out-of-sleep-at-hackgt.tech/v1/health return JSON);
  DEMO_SCRIPT.md carries the one fallback exactly as chris.md (R6
  fallback) states it (the same compose file on Chris's laptop serving
  everything on one origin, http://<laptop-ip>, the app's dist rebuilt
  for it) and it was rehearsed; the Pi keeps alarming with no cloud at
  all.
- My Irin: every one of the eleven dashboards renders from the demo
  device and from the loaded history, each with an "as of" line and the
  DEMO badge where the data is demo; the under-the-hood tile reports a
  compressed size; the agreement test is green.
- Voice: the buddy alert clip loops on the watcher page and stops on
  Call, and the same text and clip arrive as a WhatsApp message on the
  registered buddy's phone (the buddy sent "hi" to the test number
  inside the last 24 hours); a spoken echo plays from the Pi's service
  after the tone; the
  Family Story email carries the mp3; VOICE_BACKEND=none still demos.
- The Pi itself (if the audit runs with it available; else N/A): screen
  blanking is off (raspi-config reports it, and the display stays lit
  after 15 idle minutes); `timedatectl` shows the clock synchronized and
  the "clock not set" badge is gone; the backend runs as the kiosk user
  via a --user unit with linger on, and an inject-low sounds through the
  soundbar from the service; the backlight dims from the service without
  root; the four sound files exist in backend/sounds/; DEMO_SCRIPT.md's
  logistics say "hotspot up before the Pi".
- Kiosk autostart: at audit time `systemctl --user is-enabled kiosk`
  must print disabled (enabling it is Chris's last step AFTER this audit
  passes; an enabled kiosk during the audit is a finding). Read
  deploy/kiosk.sh and kiosk.service and confirm the escape hatches are
  real: the NO_KIOSK flag check in both the script and the unit's
  ConditionPathExists, the 20 s delay, Restart=no with StartLimitBurst=1,
  and no Chromium line in any autostart file. Do not enable it yourself.
