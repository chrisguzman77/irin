# /skeleton — build the day-one skeleton (run ONCE, by Chris, in the fresh repo)

You are in a nearly empty repo that already contains CLAUDE.md,
.claude/commands/, .cursor/rules/irin.mdc and AGENTS.md (generated
copies of CLAUDE.md), docs/ (the three spec PDFs, FAMILY_STORY.md,
OPEN_ITEMS.md, and plans/), hardware/docs/figures/ (the ten diagrams),
and possibly a README stub. The target Python is 3.14 (laptops natively,
the Pi through uv); write 3.13-compatible code. Your job is to create every remaining file
of the day-one skeleton so that main boots and all four owners can start
work in parallel. Create files only; do NOT git add, commit, or push —
the human reviews the diff and makes the initial commit himself.

GUARD: if backend/app/main.py already exists, this command has already
been run. STOP and say so; never overwrite a populated repo.

Read CLAUDE.md and docs/plans/chris.md first. The repository layout to
produce is the "Repository Structure" tree in the spec plus the sponsor
additions below; docs/plans/ files define the interfaces. Where this file
and those disagree, this file wins for the skeleton.

## Ground rules

- Everything must run on a laptop with IRIN_HW=mock and no
  hardware libraries installed. Guard all hardware imports.
- All timers and timestamps flow through app/clock.py. No time.time()
  or sleep() anywhere except inside clock.py itself.
- Stubs must be honest: a function that isn't implemented yet raises
  NotImplementedError or returns a clearly-labeled placeholder; it
  never silently pretends to work. Sponsor-tier stubs (rounds/, buddy/,
  relay/, clinician/, watch/) are interface-only: their docstrings name
  the plan step (R2, B3, ...) that fills them in, and nothing in the
  boot path imports them.
- Keep every file small. This is a skeleton: interfaces complete,
  behavior minimal.

## Files to create

### backend/app/contracts.py (write this FIRST, in full)
Pydantic models, complete and final enough to build against. Core:
- Reading: timestamp (datetime), glucose_mgdl (float), trend (str),
  source ("nightscout" | "replay"), is_stale (bool)
- Treatment: timestamp, kind ("bolus" | "basal" | "carbs" | "note" |
  "glp1_dose" | "therapy_change"), insulin_units (float | None),
  carbs_g (float | None), dose_label (str | None), text (str | None),
  confirmed (bool) — insulin_units may only be stored with confirmed=True
- Forecast: timestamp, predicted_mgdl (float), horizon_min (int = 30)
- AlarmState: state ("idle" | "pending" | "active" | "acknowledged" |
  "rearmed"), trigger_type ("predicted_low" | "actual_low" | "high" |
  "stale" | None), started_at, acknowledged_at (both datetime | None)
- PresenceState: mode ("home" | "away"), source ("radar" | "toggle"),
  since (datetime)
- Settings: low_threshold (default 70), high_threshold (250),
  predictive_enabled (True), predictive_lead_min (30),
  consecutive_predictions_n (2), night_window_start ("22:00"),
  night_window_end ("07:00"), high_alert_mode ("oneshot"),
  high_remind_hours (float | None = None), led_colors (dict),
  sound (str), volume (float), report_email (str | None),
  basal_time (str | None), iob_duration_hours (4.0),
  presence_override ("auto" | "home" | "away" = "auto"),
  basal_units (float | None = None), glucagon_on_hand (bool | None =
  None), glucagon_expiry (date | None = None),
  night_buddy (dict: have_buddy, be_watcher, hub_watchable,
  hub_volunteer, all False), emergency_script (EmergencyScript | None),
  family_recipients (list[FamilyRecipient] = [])
- FRESH_PIN_ENDPOINTS: a module-level tuple of the route paths that
  require a fresh keypad or prompt entry (pairing confirm, doctor-message
  confirm and decline), imported by auth.py and read by display.js and
  by the app's usePin hook (frontend/app/src) from
  /api/contracts/fresh_pin (a tiny GET).
- WSMessage: type ("reading_update" | "forecast_update" |
  "alarm_state_change" | "acknowledge" | "settings_change" |
  "treatment_logged" | "presence_change" | "mode_change" |
  "state_snapshot" | "card_sent" | "doctor_message_received" |
  "doctor_message_resolved" | "recall_due" | "symptom_check_due" |
  "pairing_state" | "plan_state" | "buddy_alert" | "hub_update" |
  "treating_set" | "family_story_pending" | "family_story_sent"),
  payload (dict). state_snapshot carries the full current state (latest
  reading, forecast, alarm state, settings, presence, mode, active_plan,
  pending_doctor_messages, todays_checkin_status, pairing_state,
  buddy_state, family_story_status, clock_synced) and is sent by the hub
  on every new connection.
Sponsor models, under "# --- Rounds ---", "# --- Night Buddy (demo
tier) ---", and "# --- Family Story ---" comments, exactly as listed
under "Contracts additions" in docs/plans/chris.md (Appendix A of the
updated Rounds spec plus the two marked additions): NightRecord (with
reason_codes and code_source), AlarmEvent (tier "predicted_low" |
"actual_low" | "stale" | "high"; ack_source "device" | "app" | None;
presence_during "home" | "away" | "unknown"), LowEvent, LowEventRecall
(answer felt_and_treated | woke_no_symptoms | dont_remember | was_awake
| None), SymptomCheck (gi), TitrationStep, WatchOptions, TitrationPlan,
SignalCard (program, kind, flags, the confidence dict), Pairing (with
peer_kind), DoctorMessage (nine kinds, including insulin_change with
insulin, new_units, start_date), BuddyLink, BuddyAlert, HubListing,
HubClaim, EmergencyScript, FamilyRecipient, FamilyStory; and, under
"# --- Owner pairing, directory, cloud, narrative ---", DevicePairing
(the owner pairing, a separate model from Pairing, whose peer_kind stays
doctor | buddy: device_id, user_id, device_url, token, paired_at,
state), UserProfile, BuddyMatch, FamilyBearer, NarrativeScope,
SecondOpinion, plus audio_url (str | None) on BuddyAlert, FamilyStory,
and the morning report model (MorningReport). Copy the field lists from
that file verbatim. All of them ship in this day-one file (decision
B11).

### backend/app/clock.py
A Clock class with now() and a speed multiplier (default 1.0). The
replay datasource sets the multiplier; everything else only calls
now(). One module-level instance `clock`.

### backend/app/config.py
Settings from environment: IRIN_HW ("mock" | "real"),
DATASOURCE ("replay" | "nightscout", default "replay"),
SCENARIO (default "demo/scenarios/the_save.csv"),
REPLAY_SPEED (default 60.0), NIGHTSCOUT_URL, NIGHTSCOUT_TOKEN, PIN,
ANTHROPIC_API_KEY (the direct-Anthropic link of the narrative chain),
SMTP_HOST, SMTP_USER, SMTP_PASS, REPORT_EMAIL (the morning report
email), and for the sponsor tiers RELAY_URL (default http://localhost:8100),
CLOUD_URL (default http://localhost:8200), RELAY_SOURCE_KEY (the Pi's
key, sent as X-Source-Key), DEVICE_ID, DEVICE_TOKEN, DEVICE_URL (what
the Pi registers as device_url at owner pairing: https://device.<DOMAIN>
on the Pi, default http://localhost:8000 on a laptop), APP_ORIGIN
(default https://irin-out-of-sleep-at-hackgt.tech, for CORS),
INBOX_URL, WATCH_URL, IRIN_BRAIN_ONLY (default false), NARRATIVE_BACKEND
(template | anthropic | backboard, default template; the first link of
narrative.py's chain), BACKBOARD_API_KEY, META_MODEL_API_KEY (the Meta
Model API, used when NARRATIVE_ROUTING names provider meta),
NARRATIVE_ROUTING (a JSON table task → [provider, model]; the eight
tasks from chris.md R13+), VOICE_BACKEND (none | elevenlabs, default
none), ELEVENLABS_API_KEY, ELEVENLABS_VOICE_DEVICE (the bedside voice),
ELEVENLABS_VOICE_ALERT (the alert-loop voice). Every name here is spelled
exactly as in .env.example (docs/plans/chris.md I2 and setup doc 09).

### backend/app/datasource/ (base.py, replay.py, nightscout.py)
base.py: DataSource interface — async get_latest() -> Reading,
history(minutes) -> list[Reading], seek(to: datetime) (a no-op that
raises NotImplementedError on nightscout). replay.py: plays a scenario
CSV at REPLAY_SPEED through clock.py; a reading older than 15
accelerated minutes is served with is_stale=True; seek(to) is a stub
naming R12 (bulk-load readings to the timestamp, advance clock.py, let
the engine's catch-up run). nightscout.py: interface-complete
stub that raises NotImplementedError("day-two work") on use.

### backend/app/main.py
FastAPI app: mounts ../frontend/display at / (StaticFiles, html=True);
the phone app is NOT mounted (it is hosted, decision 27/28), so no /app
route; CORS middleware allowing APP_ORIGIN and http://localhost:5173
(Vite's dev server) with the X-PIN and Authorization headers;
/api/health returning {"ok": true,
"datasource": ..., "hw": ...}; /api/latest returning the current
Reading from the active datasource; POST /api/mode (PIN-gated) that
swaps the active datasource at runtime between "replay" and
"nightscout" — switching to nightscout may return 501 until the
poller exists, but the endpoint and the swap mechanism are day-one;
a /ws WebSocket echo stub in ws.py that broadcasts reading updates on
a timer driven by clock.py. Boots clean with DATASOURCE=replay. Does
NOT import anything from rounds/ or buddy/.

### backend/app/ stubs (interface only)
auth.py (require_pin dependency checking a PIN header against config,
used by every mutating route from day one, no localhost exemption; plus
require_fresh_pin, the same check for the routes in
FRESH_PIN_ENDPOINTS, documented as "the frontends must obtain this PIN
from a fresh prompt, never storage"), store.py (SQLite init +
insert/select for readings and treatments, plus empty table definitions
for alarm_events, night_records, low_events, low_event_recalls,
symptom_checks, cards, plans, pairings, doctor_messages,
family_stories), family_story.py (stub; docstring names
the F-steps), alarm.py (the
state machine class with the states/transitions from docs/plans/chris.md
as a docstring and TODOs, plus a REAL, tiny on_transition(callback)
observer registry that later tiers hang off), iob.py, forecast.py,
voice.py, reports.py, scheduler.py, presence.py (state machine skeleton
with the night rule and toggle override as documented TODOs), ws.py.
forward.py (stub; docstring names C1: the outbound batched forwarder on
clock.py, never blocking the poller), voice_out.py (stub; docstring
names step 11+: ElevenLabs render → ffmpeg → wav cache, plays after a
tone). rounds/ package, the file list from the updated Rounds spec:
__init__.py, ledger.py, alarm_events.py, nights_adapter.py,
low_events.py, recall.py, cards.py, standing.py, step_watch.py,
narrative.py, noise.py, crypto.py, pairing.py, relay_client.py,
messages.py, vigilance.py — each a stub whose docstring names its
R-step (R2 recorder, R3 ledger and adapter, R4 low events, R5 crypto
and pairing, R7 relay client and cards, R8 standing and noise, R9
messages, R10 step_watch, R11 recall, R13 narrative, R14 vigilance).
buddy/ package: __init__.py, rung.py (docstring names B2), treating.py
(docstring names B4).

### backend/sounds/
make_tones.py: a small numpy + wave script (no other deps) that writes
alarm_soft.wav (soft two-tone, ~2 s), alarm_urgent.wav (louder, faster,
~2 s), chirp.wav (one short chime), and buddy_chime.wav (a distinct
three-note pattern), 16-bit mono 44.1 kHz. Run it once during the
skeleton so the four files exist and are committed (they are small);
sound.py refers to them by these names.

### backend/tests/
test_smoke.py: app imports, /api/health 200, /api/latest returns a
valid Reading in replay mode. test_replay.py: replay serves readings
in order and marks stale after a gap. Placeholder files
test_alarm.py / test_iob.py / test_voice.py / test_presence.py /
test_alarm_events.py / test_ledger.py / test_low_events.py /
test_standing.py / test_noise.py / test_step_watch.py / test_recall.py /
test_seek.py / test_narrative.py / test_crypto.py / test_pairing.py /
test_messages.py / test_vigilance.py / test_buddy.py /
test_family_story.py each containing one skipped test naming
what will go there (pytest.mark.skip with reason, quoting the named
check from docs/plans/chris.md). Never sleep(); drive clock.py.

### backend/requirements.txt
fastapi, uvicorn[standard], pydantic, pytest, httpx (test client),
python-multipart, xgboost, numpy (forecast.py imports ml/models/
predict.py, which needs both; pin xgboost to the SAME version as
ml/requirements.txt so the saved model loads identically), anthropic
and matplotlib (the morning report: the Claude API call and the graph
PNG; both run on the Pi, so they belong here, not only in ml/), pynacl
and qrcode[pil] (Rounds), httpx (the forwarder and the relay client),
backboard-sdk, openai (the Meta Model API client; the API is
OpenAI-SDK compatible, called with base_url), and elevenlabs (behind
their backend switches; harmless without keys). Nothing hardware.
Every entry must have a prebuilt aarch64 wheel (all of these do); no
pandas, no scikit-learn, no pickles on the Pi. A header comment states
the project's Python: 3.14 everywhere (the Pi through uv), code kept
3.13-compatible.
ml/requirements.txt: pandas, scikit-learn, xgboost, matplotlib, jupyter.
hardware/requirements.txt: a comment header "Pi only — do not install
on laptops. RPi.GPIO and rpi_ws281x do NOT work on the Pi 5; do not add
them." + pi5neo, adafruit-blinka, gpiozero, lgpio (commented with
versions TBD on the Pi). relay/requirements.txt: fastapi,
uvicorn[standard], pydantic, pytest, httpx, pymongo, pynacl, openai
(the Meta Model API for the buddy narratives).
cloud/requirements.txt: fastapi, uvicorn[standard], pydantic, pytest,
httpx, psycopg[binary], elevenlabs.

### relay/
main.py: FastAPI app with /v0/health returning {"ok": true, "store":
...} and every route from "Relay API v0" in docs/plans/chris.md present
as a stub returning 501 with the R-step or B-step name in the detail,
including the buddy directory routes (B3+) and the three owner-pairing
routes (POST /v0/device/pairings, POST /v0/device/pair, DELETE
/v0/device/pair, each naming R5+); store.py: pymongo against
ATLAS_URI, defaulting to mongodb://localhost:27017 (the compose
container), with the collection names from chris.md R6+ and the two TTL
indexes created at boot; directory.py: stub (B3+); hub.py: stub (B3);
notify.py: stub (B4+; the WhatsApp Cloud API channel, reads
WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID, never a glucose value in a
payload). The relay reads RELAY_SOURCE_KEYS (comma-separated device
keys, checked as the X-Source-Key header), RELAY_ADMIN_KEY (for the
demo-only Spark simulation), RELAY_KEY (encrypts emergency numbers at
rest), NARRATIVE_BACKEND, NARRATIVE_ROUTING, BACKBOARD_API_KEY,
META_MODEL_API_KEY and ANTHROPIC_API_KEY (the same four-link narrative
chain as the Pi, for the buddy line, the match explanation and the
emergency script), WHATSAPP_TOKEN, and WHATSAPP_PHONE_NUMBER_ID from the
environment. README.md: what the
relay is (a blind courier: it stores ciphertext only and can never read
a card), the "Clinical Signal Card v0" JSON Schema generated from
SignalCard (one schema, two programs; the program field decides which
sections a renderer shows; any device maker could publish cards into
this channel), the versioned route list, the envelope fields
(recipient_id, sender_id, nonce, ciphertext, source, kind, program,
is_demo), and the auth model (source key for devices, bearer for inbox
and watcher). tests/test_relay.py: /v0/health 200 plus one skipped
placeholder naming the R8 checks.

### cloud/
main.py: FastAPI app on port 8200 with /v1/health and every route from
chris.md C1, C3, and B4+ (ingest, dash/{name}, family/last_night,
family/bearers, audio/render) as 501 stubs naming their step; ingest
validates the device token against DEVICE_ID and DEVICE_TOKEN read from
cloud's OWN environment (the server's .env; one hackathon device);
audio/render reads VOICE_BACKEND, ELEVENLABS_API_KEY and
ELEVENLABS_VOICE_ALERT (the buddy clip the relay links to over WhatsApp);
migrate.py: applies cloud/sql/*.sql in order against TIGER_URI
(defaulting to the compose's timescaledb) once, idempotently; sql/:
001_hypertables.sql, 002_aggregates.sql, 003_policies.sql as commented
stubs whose headers copy george.md step 9; audio.py, dash.py, ingest.py:
stubs; README.md: the ingest payload, the dashboard names and their
aggregates, the family bearer, the audio endpoint, and the rule that
nothing in cloud/ is ever read by a card. tests/test_ingest.py and
test_dash.py: one skipped placeholder each naming the C-step checks.

### hardware/
hal.py: get_hal() returns MockHAL when IRIN_HW != "real".
Interface: set_leds(state: str), play_sound(name: str, volume: float),
stop_sound(), get_presence() -> bool | None (None = no value: mock not
driven, or read error), set_display_brightness(level).
mock.py: MockHAL logging calls, get_presence() returning None until
set_presence_for_test(bool) drives it, so tests and the demo panel own
presence explicitly. leds.py /
sound.py / presence.py: real-HW stubs behind guarded imports. scripts/:
first_light.py, full_frame_test.py, radar_watch.py, speaker_test.sh as
commented stubs. docs/wiring.md: the pin map copied from
docs/plans/slavik.md, referencing the figures by filename.
docs/figures/: the ten spec diagrams, fig01_pi5_landmarks.png through
fig10_screw_terminal.png (Chris drops them in beside the spec PDFs
before running this command; if they are missing, create a README.md
listing the ten expected names and stop short of inventing images).
Slavik adds radar_wiring_*.jpg photos later.

### frontend/
display/index.html + style.css + display.js: dark page, large
placeholder glucose number wired to /api/latest via fetch polling
every 5 s, and a note "WebSocket wiring: Chris". Plain HTML, served by
the Pi's FastAPI at /.

app/: the hosted four-tab app is a Vite + React + TypeScript + Tailwind
project (decision 28). Write the scaffold files BY HAND, exactly as
`npm create vite@latest -- --template react-ts` plus Tailwind would lay
them out, so Justin's first `npm install` just works; do NOT run npm
yourself (Node may not be on this laptop):
- package.json: name "irin-app", private, "type": "module", scripts
  dev (`vite`), build (`tsc -b && vite build`), preview, typecheck
  (`tsc --noEmit`), and types (`openapi-typescript
  http://localhost:8000/openapi.json -o src/types/pi.d.ts &&
  openapi-typescript http://localhost:8100/openapi.json -o
  src/types/relay.d.ts`); dependencies react, react-dom, d3;
  devDependencies vite, @vitejs/plugin-react, typescript, tailwindcss,
  @tailwindcss/vite, @types/react, @types/react-dom, @types/d3,
  openapi-typescript. Use caret ranges of the current majors (React 19,
  Vite 7, Tailwind 4, TypeScript 5); Justin's `npm install` resolves
  exact versions and commits package-lock.json.
- vite.config.ts: plugins react() and tailwindcss(); base "/".
- tsconfig.json + tsconfig.app.json + tsconfig.node.json as the Vite
  react-ts template writes them (strict on).
- index.html (root, loads /src/main.tsx, links public/manifest.json).
- src/main.tsx, src/App.tsx (the code gate reading/writing
  sessionStorage and sending it as the X-PIN header; four tabs Irin
  Device, My Irin, Irin Buddy, Irin Rounds with the active one in
  localStorage; each tab a placeholder component under src/tabs/; a
  "Pair your Irin" button on the Device tab), src/config.ts (exports
  RELAY_URL and CLOUD_URL from import.meta.env.VITE_RELAY_URL /
  VITE_CLOUD_URL, defaulting to https://api.<DOMAIN> and
  https://cloud.<DOMAIN>, and DEVICE_URL_OVERRIDE from VITE_DEVICE_URL,
  undefined unless set), src/index.css containing only
  `@import "tailwindcss";`, src/types/README.md ("generated by npm run
  types; never edit; regenerate when contracts.py changes"),
  src/assets/README.md ("timezones.geojson goes here, simplified with
  mapshaper; bundled, never a CDN at demo time"), src/vite-env.d.ts.
- public/manifest.json (name Irin, standalone display) and a
  public/favicon.svg placeholder.
- .env.production (committed, public values only):
  VITE_RELAY_URL=https://api.<DOMAIN> and
  VITE_CLOUD_URL=https://cloud.<DOMAIN> (VITE_FB_APP_ID joins it only if
  the optional Facebook Login ships; it is public too); .env.example
  listing those names with localhost values for .env.development.local,
  plus VITE_DEVICE_URL (development only, commented out by default:
  when set and the backend's /api/health reports hw: mock, the Device
  tab uses it directly, skips pairing, and shows a small DEV badge) and
  VITE_FB_APP_ID (commented).
- dist/index.html: a static placeholder page ("Irin — app build
  pending; Justin runs npm run build") so Caddy has something to serve
  from the first deploy. Justin's first build replaces it; dist/ is
  committed from then on.
- app/README.md: the three commands (npm install; npm run types; npm
  run dev), the rule that dist/ is rebuilt and committed before every
  deploy, and that adding a dependency is Justin's decision.

family/index.html + style.css + family.js + config.js:
placeholder with config.js exporting CLOUD_URL and FAMILY_BEARER
placeholders. clinician/index.html + style.css + inbox.js + config.js:
placeholder titled "Irin Rounds — clinician inbox (mock Ascend)" with
config.js exporting RELAY_URL; watch/index.html + style.css + watch.js +
config.js: placeholder titled "Night Buddy — watcher (concept)" with
the same config shape. These three and display/ stay plain HTML with no
build step. Keep all of it minimal and ugly; Justin owns the looks.

### journal/ (four files: chris.md, justin.md, george.md, slavik.md)
Each exactly:
"Updated: <today> 00:00" then the five headers with:
Done: nothing yet / In progress: nothing / Broken: nothing /
Interface changes: none / Notes for other models: none

### demo/
scenarios/the_save.csv: SYNTHETIC placeholder you generate — 8 hours
of 5-minute readings, in-range overnight drifting to a low around
hour 6 (down to ~55), recovering after. Columns: timestamp,
glucose_mgdl, trend. Timestamps start 2020-01-01 (obviously fake).
A comment row is NOT allowed in CSV — instead note SYNTHETIC in
demo/scenarios/README.md, which states these are placeholders until
George's date-shifted real scenarios replace them, and which documents
the companion JSON schema every scenario may ship with
(<name>.json next to <name>.csv): scenario, kind ("core" |
"basal_change" | "titration" | "buddy"), synthetic (bool),
overlay_synthetic (bool), night_window {start, end}, reason_codes (per
night: codes and code_source, "inferred" on history), plan
(TitrationPlan | null), dose_change ({date, insulin, new_units} | null,
the confirmed change the Follow-up compares around), symptom_checks
(list of SymptomCheck), injections (list of glp1_dose Treatments),
recall_answers (per low event), alarm_events (list of AlarmEvent, the
glucose-side fields inferred from the data, the response-side fields an
overlay). make_scenarios.py: stub. DEMO_SCRIPT.md: headers for the run
of show — the main 3-minute slot, the Rounds eight beats (pairing,
detect, decide, verify, the therapy start, the amber card, the pharma
moment, close), the Night Buddy 45-second beat, the Family Story video
beat — content TBD except two lines the skeleton writes now, verbatim:
under the Rounds detect beat, "Say it this way, never unqualified:
glucose real; reason codes inferred and labeled; acknowledge, presence,
and recall data are a labeled overlay."; and a "Q&A sheet" section
holding that same qualifier as its first entry and, as its second, the
emergency-script production answer: "Demo tier: the script is stored on
the relay encrypted at rest and released only to the live claim-holder.
Production: at claim time the relay notifies the device, which is by
definition awake during an episode, and the device encrypts the script
to the claim-holder's public key on demand, so it is end-to-end even to
a volunteer unknown in advance." The Rounds Q&A ammunition and the Night
Buddy Q&A table from the challenge specs are pasted in by Chris at R13.

### ml/
clean_clarity.py, build_dataset.py, train.py, evaluate.py,
label_history.py, events.py as argparse stubs whose docstrings copy
their step from docs/plans/george.md. data/README.md: "raw Clarity
exports (data/raw/Clarity_Export_*.csv), clean.csv, therapy_changes.txt,
and nights_labeled.csv live in data/, gitignored, never committed —
personal medical data." Create data/raw/ with only a .gitkeep.
models/features.py: the single shared feature function (stub with the
feature list from george.md as a docstring), imported by both
build_dataset.py and predict.py. models/predict.py: the contract from
george.md — load_model(), predict(window: list[float]) -> float,
computing features ONLY via features.py, raising a clear error until
forecast_v1.json exists. models/nights.py: the five signatures from
george.md step 4 (classify_night, night_metrics, low_events,
standing_window, step_window_metrics), numpy only, raising
NotImplementedError. evaluate_rounds.py: an argparse stub whose
docstring names the three measured numbers from george.md step 6 (lead
time, false-alarm rate, detection lag). tests/test_nights.py: one
skipped placeholder naming the worked examples.

### Root files
.gitignore: .env, *.db, backend/irin.db, backend/keys/, backend/sounds/generated/, cloud/audio_cache/,
ml/data/, __pycache__/, *.pyc, .venv/, node_modules/, *.local, .DS_Store,
*.mp4, *.mov. NOT ignored, on purpose: frontend/app/dist/ and
frontend/app/.env.production (decision 28). .env.example: PIN, NIGHTSCOUT_URL, NIGHTSCOUT_TOKEN,
ANTHROPIC_API_KEY, SMTP_HOST, SMTP_USER, SMTP_PASS, REPORT_EMAIL,
RELAY_URL, CLOUD_URL, RELAY_SOURCE_KEY and RELAY_SOURCE_KEYS (with the
comment: the Pi sends RELAY_SOURCE_KEY as X-Source-Key; the relay
accepts the comma-separated RELAY_SOURCE_KEYS; one value serves both),
RELAY_ADMIN_KEY, RELAY_KEY, DEVICE_ID, DEVICE_TOKEN, DEVICE_URL,
APP_ORIGIN, INBOX_URL, WATCH_URL, IRIN_BRAIN_ONLY, NARRATIVE_BACKEND,
BACKBOARD_API_KEY, META_MODEL_API_KEY, NARRATIVE_ROUTING,
WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID (server only), VOICE_BACKEND,
ELEVENLABS_API_KEY, ELEVENLABS_VOICE_DEVICE, ELEVENLABS_VOICE_ALERT,
ATLAS_URI, TIGER_URI, DOMAIN, and commented TWILIO_* lines marked "full
tier only" — placeholder values only. README.md: what Irin is (three
sentences: the device, and the two sponsor entries as doors into the
same system), the quickstart (clone, venv, pip install -r
backend/requirements.txt, IRIN_HW=mock uvicorn app.main:app --reload
from backend/, open /; for the app: Node 22, cd frontend/app, npm
install, npm run dev; optionally the relay from relay/ and the static
role pages), pointer to CLAUDE.md, docs/plans/, the three
spec PDFs, and docs/FAMILY_STORY.md. LICENSE: MIT, current year, team names. docs/META_WRITEUP.md:
the four headers from the Night Buddy spec (Who it is for / How it
strengthens connection / Why AI is essential / Safety posture) with a
"video link:" line, content TBD. deploy/: install.sh (a commented
stub whose header lists its steps in order: check `uname -m` is aarch64
and abort otherwise; install uv and `uv python install 3.14`; `apt
install liblgpio-dev swig build-essential ffmpeg chromium`; `uv venv
--python 3.14 .venv` and `uv pip install` both requirements files (the
3.13 escape hatch documented beside it); enable SPI; add the kiosk
user to spi, gpio, audio, video; `raspi-config nonint do_blanking 1`;
write /etc/udev/rules.d/90-backlight.rules making
/sys/class/backlight/*/brightness group video and g+w; install
irin.service as a systemd --user unit for the kiosk user and
`loginctl enable-linger` it; install kiosk.service DISABLED and print
"kiosk autostart is off; enable by hand after /audit"), irin.service (a --user unit,
WorkingDirectory backend/, ExecStart uvicorn, Restart=always,
After=network-online.target), kiosk.sh (the launcher: exit 0 if
/boot/firmware/NO_KIOSK exists; sleep 20 unless `--now`; exec chromium
`--kiosk --noerrdialogs --disable-infobars
--disable-session-crashed-bubble http://localhost:8000/`),
kiosk.service (a --user unit: ExecStart=kiosk.sh, After=irin.service,
ConditionPathExists=!/boot/firmware/NO_KIOSK, Restart=no,
StartLimitBurst=1; NEVER enabled by install.sh or by any Claude — Chris
enables it by hand after the final audit), pi_setup.md (the no-RTC
rule: boot with the network up; the touch-rotation note; the three
Phase 9 checks; the kiosk rule and its three escape hatches: the
NO_KIOSK flag from a tty, SSH, or an SD card reader; the 20 s delay;
SSH underneath; the uv Python 3.14 install and the 3.13 escape hatch;
ffmpeg), docker-compose.yml (services caddy, relay, cloud, mongo,
timescaledb; the relay and cloud read ATLAS_URI and TIGER_URI from the
env and default to the two local containers; Caddy mounts the Caddyfile
and serves frontend/app/dist, clinician, watch, family as static sites),
Caddyfile (irin-out-of-sleep-at-hackgt.tech → /srv/app, the mounted
frontend/app/dist, with `try_files {path} /index.html` so tab URLs
survive a refresh; api.irin-out-of-sleep-at-hackgt.tech → relay,
cloud.irin-out-of-sleep-at-hackgt.tech → cloud, doctor., watch., family.
→ their folders; DOMAIN from the env),
vultr.md (VPS setup: Docker, DNS records, .env, compose up, the health
URLs), atlas.md (the cluster, Nightscout's MONGODB_URI, the relay
collections), sync_rules.sh (regenerates .cursor/rules/irin.mdc and
AGENTS.md from CLAUDE.md with a one-line header),
and public_mirror.sh, a commented stub whose header lists exactly what it
must do on submission day (Chris): export the working tree at the
chosen commit into a temp directory, drop journal/ and AUDIT.md, trim
ml/models/metrics.md to its headline block, init a fresh repo with ONE
squashed commit, grep the tree for .env, keys, ml/data, raw timestamps,
and any unqualified "real night" claim, and push to the public mirror
named in the Meta form. The working repo stays private.

## Verification (the task is not done until these pass)

1. From backend/: IRIN_HW=mock uvicorn app.main:app --reload
   boots with no traceback; / renders; /api/health, /api/latest and
   /openapi.json return JSON. frontend/app/dist/index.html exists (the
   placeholder) and frontend/app/package.json parses as JSON; do not run
   npm.
2. From backend/: pytest passes (skips allowed, failures not).
3. From relay/: uvicorn main:app --port 8100 boots and /v0/health
   returns JSON; pytest passes from relay/. From cloud/: uvicorn
   main:app --port 8200 boots and /v1/health returns JSON; pytest passes
   from cloud/ (skips allowed). `docker compose -f deploy/docker-compose.yml
   config` validates.
4. git status shows NO file matching .gitignore patterns.
5. Print a tree of what was created for the human to review.

Then STOP. Overwrite journal/chris.md with a real snapshot (Done: day-one
skeleton; In progress: nothing; next action: human reviews and makes the
initial commit). The human commits to main, creates dev from main
(git checkout -b dev && git push -u origin dev), pushes both, and turns
on GitHub branch protection for main (require pull request, no direct
pushes) so only a human can ever promote dev to main.
