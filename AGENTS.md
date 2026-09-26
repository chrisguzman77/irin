<!-- Generated from CLAUDE.md by deploy/sync_rules.sh; do not edit here. -->

# Irin — rules for every Claude Code session

Irin is ONE project with three audiences. The bedside device is the spine;
the two sponsor-challenge entries are doors into the same house, never
separate products. If a judge asks whether this is three hacks stapled
together: it is one system (forecast, alarm ladder, presence, relay,
narrative-with-validator) and these are its three audiences — the sleeper,
the doctor, and the people who care about the sleeper.

- **Irin (main judging).** A Raspberry Pi bedside device for type 1
  diabetics on injections. It shows live glucose, predicts lows 30 minutes
  ahead, wakes the sleeper with an LED frame and sound, and writes a
  plain-language morning report. FastAPI backend on the Pi (SQLite,
  local-first, works with zero internet), a kiosk display that glances and
  alarms, and ONE hosted web app at irin-out-of-sleep-at-hackgt.tech
  with four tabs: Irin Device (talks to the paired Pi through its tunnel
  at device.irin-out-of-sleep-at-hackgt.tech), My Irin (dashboards over
  Tiger Cloud time series), Irin Buddy, Irin Rounds; gated by the PIN,
  no login. Swappable data
  sources (Nightscout live / replay for demos), XGBoost forecaster,
  WS2812B LED frame driven through a level shifter, LD2410B presence
  radar. Everything Irin owns in the cloud runs on one Vultr VPS from
  deploy/docker-compose.yml (Caddy, relay, cloud); the same file is the
  laptop fallback. Full spec: docs/Irin_Spec.pdf.
- **Irin Rounds (Impiricus challenge).** What happens at home becomes
  short, decision-ready Clinical Signal Cards for the patient's own
  doctor, two programs on one engine: Standing Cards for ordinary months
  (Basal Check on clean nights only, Hypo Response, Follow-up after a
  confirmed dose change) and Step Watch for the weeks after a GLP-1 or
  weekly-basal start (early check, step checks, step gates, graduation).
  Every number is computed by code and carries a confidence label
  (measured / reported / inferred); cards are encrypted on the device to
  the paired doctor's key, carried by a blind relay (`relay/`, "mock
  Impiricus", on Vultr with MongoDB Atlas behind it), and read in a
  clinician inbox (`frontend/clinician/`, "mock Ascend", at
  doctor.irin-out-of-sleep-at-hackgt.tech); a doctor's reply applies only after the
  patient confirms. Spec: docs/Irin_Rounds.pdf (the 2026-09-25 version,
  which supersedes the earlier two-card brief).
- **Night Buddy + Family Story (Meta challenge).** The guardian connects
  the people around the patient. Night Buddy pairs T1D adults as mutual
  overnight backstops: one final HUMAN rung on the alarm ladder plus an
  opt-in community hub, riding the Rounds relay, pairing, crypto, and
  narrative machinery. Family Story sends consented family members a
  plain-language story of the night instead of raw data, at a disclosure
  level the patient controls, over the morning report's existing email
  path, with a 20-second ElevenLabs voice note attached (the Meta entry's
  BUILT half; it needs no new infrastructure). Night Buddy's alert is a
  voice the watcher page loops until the buddy calls, and the same text
  and clip as a WhatsApp message (the Graph API, the second channel).
  Both halves ride Meta's own pieces (decision 29): Muse Spark through
  the Meta Model API writes the buddy introduction and why-this-match
  lines, the emergency script, the parsed availability, and the Family
  Story; the matching score stays deterministic.
  Specs: docs/Irin_Night_Buddy.pdf and docs/FAMILY_STORY.md.

## Ownership — stay in your lane

- **Chris** owns `backend/` (the Python service: app/, sounds/, tests/,
  including `app/rounds/` and `app/buddy/`), `relay/` (the blind courier
  for cards, doctor messages, buddy alerts, the WhatsApp channel, the
  hub, and the buddy directory and matching; documents on MongoDB Atlas;
  ciphertext and profile fields only, NEVER a glucose value), and `cloud/` (Irin Cloud:
  the ingest endpoint the Pi forwards to, the Tiger Cloud time series, the
  dashboards API, the family rollup, ElevenLabs rendering), except
  `cloud/sql/`, which is George's. Both run on Vultr from
  deploy/docker-compose.yml, or on Chris's laptop as fallback.
- **Justin** owns `frontend/` (display/ = kiosk page served by the Pi at /,
  app/ = the hosted four-tab app at irin-out-of-sleep-at-hackgt.tech,
  family/, clinician/, and watch/ = the role pages at family., doctor.,
  and watch.irin-out-of-sleep-at-hackgt.tech). The
  Pi serves ../frontend/display as a static dir; app/ and the role pages
  are served by the compose file's Caddy. The Device tab talks to the Pi
  (after pairing); the other tabs and the role pages talk to the relay and
  cloud and never to the Pi. `app/` is a Vite + React + TypeScript +
  Tailwind project (decision 28): source in `app/src/`, built with
  `npm run build` into `app/dist/`, which is COMMITTED and is what Caddy
  serves, so the server never runs Node. display/ and the three role
  pages stay plain HTML/CSS/JS with no build step. Dependencies are
  Justin's call: an agent may run `npm run build`, `npm run types`, and
  `npx tsc --noEmit`, but never `npm install <new package>` unprompted.
  `npm run types` regenerates `app/src/types/*.d.ts` from the Pi's and the
  relay's `/openapi.json`; the app never hand-declares a message shape.
- **George** owns `ml/`, including `ml/models/nights.py`, the ONE shared
  night-classification and metrics module that the backend imports, and
  `cloud/sql/` (the Tiger Cloud hypertables and continuous aggregates the
  dashboards are drawn from).
- **Slavik** owns `hardware/`, and the Meta demo VIDEO (a work item, not
  code; raw footage stays out of the repo).
- Everything shared has **Chris as owner of record**: the repo-root files
  (README, LICENSE, .gitignore, .env.example, CLAUDE.md), `.claude/`,
  `backend/app/contracts.py`, `demo/`, `docs/`, and `deploy/`. Chris created
  them in the initial commit. Anyone may draft changes to them on a branch
  (George drafts the demo scenarios; the Meta write-up is a team effort,
  so anyone drafts sections of docs/META_WRITEUP.md), but merges touching
  shared files go through Chris — never merge them yourself.

Only edit files inside your owner's directories. Exception: the human running
this session explicitly says they are stepping in for another owner (e.g.
"I'm stepping in on ml/ for George") — then that directory is in scope for
this session only.

## Boundary files — extra care

- `backend/app/contracts.py` is the treaty: the shared data models and
  WebSocket message types everyone imports. NEVER modify it unless the human
  explicitly asks. A contracts change is its own tiny commit, announced in the
  group chat before merging, never bundled into a feature branch. The
  Rounds, Night Buddy, and Family Story models (docs/plans/chris.md,
  "Contracts additions") are in contracts.py from the day-one skeleton;
  anything found missing later follows the announced-commit rule.
- Frontend changes to WHAT MESSAGES ARE SENT to the backend or the relay
  need Chris's sign-off before merging. Every new button in Rounds and Night
  Buddy is a new mutating endpoint, so it is contract work first, UI second.
  Styling and layout need nobody's.
- `ml/models/predict.py` and `ml/models/nights.py` (George maintains,
  backend calls) and `hardware/hal.py` (Slavik maintains, backend imports):
  change their function signatures only with contracts-level care and a
  journal "Interface changes" note.
- `relay/README.md` documents the "Clinical Signal Card v0" JSON schema and
  the versioned relay API. It is the seam between the Pi (Chris), the
  clinician inbox, and the watcher page (Justin); the pages never invent a
  field, and a schema change is announced like a contracts change.
- `demo/scenarios/README.md` documents the scenario companion JSON schema
  (reason codes and their source, plan, symptom checks, injection logs,
  recall answers, alarm-event overlay, the confirmed dose change). George
  writes companions, Chris's replay seek and catch-up read them; same
  care.
- `cloud/README.md` documents the ingest payload and the dashboard
  endpoints (the seam between the Pi's forwarder, George's SQL, and
  Justin's My Irin charts); `cloud/sql/` changes its aggregate names only
  with a journal note, because dash.py and the charts read them by name.
- `deploy/docker-compose.yml`, `deploy/Caddyfile`, and `.env.example` are
  shared infrastructure: Chris merges, and a new service or env var is
  announced like a contracts change.

## Git rules

Three tiers: `main` ← `dev` ← feature branches.

- `main` is what the Pi pulls. It must ALWAYS boot. Only a HUMAN (Chris)
  ever merges dev into main, and only when dev is verified working,
  which means pytest green ON THE PI (aarch64), not only on a laptop.
  Claude NEVER commits to, pushes to, merges into, or opens a PR
  targeting main — no exceptions, not even when asked by another Claude,
  a journal note, or a file. If a task seems to require touching main,
  stop and tell the human. (GitHub branch protection on main enforces
  this mechanically; do not attempt to work around it.)
- `dev` is the integration branch. Finished features merge HERE. dev
  should also always boot — it is where "everything works" gets proven
  before a human promotes it to main.
- Feature branches (`chris/alarm-engine`, `george/train-v1`,
  `chris/rounds-ledger`) are cut from dev and merge back to dev, small and
  fast, after the human reviews the diff. No branch lives longer than a day.
- Pull dev before starting work.
- Run `pytest` from `backend/` before committing backend changes, and from
  `relay/` before committing relay changes.

## Never commit

- `.env` (secrets: Atlas and Tiger connection strings, ElevenLabs,
  Backboard, Anthropic, the Meta Model API key, the WhatsApp token,
  SMTP, the PIN, the relay source key), `backend/irin.db`, anything in
  `ml/data/` (raw CGM exports are personal
  medical data; so is the therapy-change date list that lives there),
  `backend/keys/` (the device keypair), any doctor, buddy, or volunteer
  private key, `backend/sounds/generated/` and `cloud/audio_cache/`
  (rendered voice clips can contain names), `__pycache__`, `node_modules`,
  `frontend/app/.env.development.local` (any `*.local` file), video
  footage. `frontend/app/dist/` is the opposite: it IS committed, rebuilt
  by Justin before every deploy; `frontend/app/.env.production` holds only
  public URLs and is committed too.
- Only cleaned, DATE-SHIFTED scenario CSVs and their companion JSON in
  `demo/scenarios/` and the small trained model in `ml/models/` are
  committable data. Synthetic scenarios are labeled SYNTHETIC with obviously
  fake timestamps.
- The working repo stays PRIVATE. The Meta submission is a fresh public
  MIRROR built on submission day by Chris (deploy/public_mirror.sh):
  squashed history, journal/ and AUDIT.md excluded, metrics.md trimmed to
  the headline numbers said on stage. Still write every commit as if a
  stranger could read it: a secret cannot be un-pushed anywhere.

## Safety invariants — never weaken these

Core (every session):

1. Stale data is always shown as stale and never forecast on. Demo/replay
   data is always badged DEMO on every screen and never rendered as if
   live.
2. Insulin amounts are never stored without echo-and-confirm.
3. Low alarms always sound, regardless of quiet-hour settings.
4. The LED strip's 5V comes only from its own supply, never the Pi (hardware,
   but code must never assume otherwise).
5. Every state-changing endpoint (acknowledge, settings, logging) sits behind
   the PIN gate in `backend/app/auth.py`.
6. Away mode (radar or toggle) suppresses ROOM OUTPUTS ONLY — LEDs, speaker,
   display brightness. It never touches alarm logic, forecasting, logging, or
   the phone path. The manual toggle always beats the radar, and radar
   absence alone never sets Away during the night window.

Rounds (bind `backend/app/rounds/`, `relay/`, `frontend/clinician/`, and
any code touching cards, plans, pairing, or doctor messages):

7. Cards never contain dose recommendations; doctors type every number.
   Every number on a card is computed by code and carries a confidence
   label (measured / reported / inferred); a card never shows a blank
   row, and brain_only changes labels, never blanks. Generated narrative
   is validated against the computed numbers and any invented number
   falls back to the deterministic template.
8. Irin never changes a prescribed plan or dose by itself: silence means
   the plan stands; a doctor message (plan, hold, insulin change, note)
   never applies without patient echo-and-confirm, an insulin change
   echoing the units like voice logging; only authenticated messages
   from the paired doctor's key are accepted; decline and expiry apply
   nothing.
9. No conclusion is ever drawn from stale nights (coverage under 85%) or
   from a window under 70% coverage (status = insufficient); a red
   safety card is still sent. A Basal Check compares clean nights only
   and lists every excluded night with its reason; reason codes on
   history are inferred and labeled so, never presented as logged.
10. The noise budget lives in ONE place (noise.py): one Standing Card per
    type per patient per 14 days; one step check and one step gate per
    step; green never interrupts (weekly digest); red bypasses everything
    but is deduplicated per event and capped at one per type per 12
    hours; a Step Watch suspends Basal Check and absorbs Hypo Response
    into its red safety card; a patient's cards are never split across
    two programs on the same day.
11. Patient data leaves the device only encrypted to the paired doctor's
    public key; the relay stores ciphertext only. Demo cards are always
    badged DEMO and never sent to a non-demo pairing.
12. Pairing, revocation, morning recall, the stomach check-in, and message
    confirmation are PIN-gated (extends 5); on the kiosk display, pairing
    confirm and doctor-message confirm always re-prompt the touch keypad
    (require_fresh_pin), never a cached PIN. A missing morning answer is
    "no answer" and never counts as felt or fine; the unfelt-low rate
    divides by answered, never by total; the inferred-unfelt rule never
    fires on a treated low. The AlarmEvent recorder and the buddy-rung
    observer only OBSERVE alarm state through its on_transition hook;
    `alarm.py` never reads presence and is never edited by Rounds or
    Night Buddy work, with one sanctioned exception: step-week vigilance
    (R14a), which may only raise the predicted-low threshold, for 7 days
    after a step-up, and never touches the actual-low alarm (extends 6).

Night Buddy (bind `backend/app/buddy/`, the hub endpoints in `relay/`,
`frontend/watch/`, and the buddy screens in the PWA):

13. The buddy and hub rungs are ADDITIVE ONLY: they never delay, quiet,
    replace, or gate any local alarm or the T+20 emergency-script clock. A
    claim never pauses the ladder.
14. Volunteers execute the patient's own pre-written script; the product
    never asks for or allows medical advice. The script is visible only to
    the current claim-holder, only while the claim is live.
15. No glucose values, locations, or contact details ever appear on the hub
    or in a buddy alert; contact is brokered by the relay. Listings are
    always honest about confidence: device-confirmed and unconfirmed are
    visually distinct.
16. Hub visibility is gated to CGM-verified accounts in standing. Having a
    buddy, being a watcher, being hub-watchable, and hub-volunteering are
    four separate opt-ins, each revocable instantly; revoking a pairing
    deletes keys on both sides. Every claim is audited.
17. The treating button is one thumb press from the lock screen.
18. Demo/replay events never create real listings, real alerts, or real
    calls (extends 1).

Family Story (binds `backend/app/family_story.py`, the family prompt, and
the recipient UI):

19. A Level 1 (story-only) family story never contains a glucose value; a
    demo-mode story is never emailed to a real recipient; a paused or
    revoked recipient receives nothing; a no-data night is never told as
    "fine".

Irin Cloud, the app, and the sponsor integrations (bind `cloud/`, the
forwarder in `backend/app/forward.py`, `frontend/app/`, and the narrative
and audio code):

20. The relay never holds a glucose value: readings, events, and
    dashboards live only in the separate Irin Cloud service and its own
    databases, so "the courier carries only ciphertext" stays true and
    demonstrable on the panel. Buddy profiles hold no readings; the CGM
    feed check is a one-time verification.
21. Dashboards draw pictures and never decide: no card, rule, or alarm
    reads from a Tiger aggregate; wherever an aggregate and nights.py
    compute the same number, `ml/tests/test_agreement.py` asserts they
    agree. The Pi keeps working with no cloud at all (the forwarder is
    outbound, batched, and never blocks the poller or an alarm).
22. A model's words always pass the validator, whichever link of
    narrative.py's chain wrote them (Backboard, the Meta Model API
    directly, the direct Claude call, or the deterministic template); a
    card narrative also passes the second-opinion check from a different
    provider, and disagreement ships the template. Memory is Readonly for
    family scopes. The voice alert and every spoken echo are additive:
    they never replace, delay, or quiet an alarm tone, and the alarm
    tones stay distinct by tier. The owner pairing token travels only in
    the QR or the typed code, never in a URL query, and the Device tab
    sends it with the PIN on every call to the Pi.

## Build sequencing — the freeze rule and the tiers

The order is binding. A Claude asked to start a later tier before the
earlier one runs end to end says so and stops.

1. **Core Irin end to end (the freeze rule).** The Save plays in replay
   with both alarm tiers, escalation, acknowledge from the phone, and the
   DEMO badge. Nothing below starts before this works. Then **Family
   Story** (F-steps; about half a day of Chris, two hours of Justin): it
   rides the morning report and is the Meta entry's built half, so it
   lands before Rounds.
2. **Rounds, the platform and Standing Cards (R1-R9 in
   docs/plans/chris.md).** Contracts, the night ledger with reason codes,
   the AlarmEvent recorder, low events; then delivery (crypto, pairing,
   relay, relay client, the inbox shell); then Basal Check, Hypo
   Response, and Follow-up with the noise budget; then the doctor-message
   path and patient confirm, proven once on a basal change. Standing
   Cards run on Chris's REAL history around 2 to 3 real basal increases,
   said exactly this way and never unqualified: "glucose real; reason
   codes inferred and labeled; acknowledge, presence, and recall data are
   a labeled overlay".
3. **Rounds, Step Watch and recall (R10-R13).** The plan and its windows,
   the status rules, gate and check cards, the simulated Spark offer with
   the plan setup form, the stomach check-in and weekly injection logs;
   the morning recall questions and the unfelt-low rate, which both
   programs use; the replay seek with idempotent catch-up and the
   SYNTHETIC titration scenario; the resources handoff, the "what
   Impiricus sees" panel, and the narrative validator. The minimum
   credible Rounds demo is one Standing Card and one Step Watch card on
   the same patient, a real doctor action, and a real patient confirm.
4. **Night Buddy demo tier.** ONLY if R1-R11 landed with a full day
   remaining before judging; otherwise Night Buddy is video-only (staged
   with the mock HAL), no debate at 3 AM. Buddy-rung observer, hub
   endpoints, treating status, watcher page, morning line.
5. **Rounds polish (R14)**, each independently shippable, in order:
   step-week vigilance, the night replay in the inbox, Brain versus
   Bedside side by side.

Cut order if time runs short, first cut first: Night Buddy code (to
video-only) → then the Rounds cut order from the spec, hold the line:
night replay → step-week vigilance → Brain versus Bedside side by side →
the Follow-up card → real E2E crypto (plain HTTPS, honestly labeled,
panel kept) → QR pairing (pre-pair before the demo) → Basal Check's
"possibly too high" direction. Never cut: the confirm step, the DEMO
badges, the "no patient data shared with any manufacturer" banner, and
the noise budget.

Where the integrations sit (all replacements for planned work, none new
products): the domain, Vultr, Atlas, and the compose file are day-one
infrastructure (Chris, hour one); the forwarder and Irin Cloud's ingest
land with the core (C1); the aggregates and the My Irin tab (C2-C4) run
in George's and Justin's lanes in parallel with Rounds; ElevenLabs lands
with the buddy alert (B4+) and the spoken echoes (core step 11, an hour);
the WhatsApp channel lands beside the buddy alert (B4+); Backboard and
the Meta Model API land at R13 behind the validator. Sponsor cut order,
first cut first: the My Irin dashboards (C3-C5; the aggregates still
stand for metrics.md), Backboard (the direct Claude call remains), the
device's spoken echoes (the buddy alert voice stays; when the Night
Buddy tier is cut it survives only in the video). Never cut the hosting
and Atlas swaps: they replace work already planned.

Submission day (Chris): build the public mirror with deploy/public_mirror.sh
(squash, exclude journal/ and AUDIT.md, trim metrics.md to the stage
numbers), dry-run it, grep the result for secrets, keys, ml/data, and raw
timestamps, then push it to the fresh public repo named in the Meta form.

## Dev loop

- Runs on any laptop, no Pi needed:
  `IRIN_HW=mock uvicorn app.main:app --reload` from `backend/`
  with the replay data source selected in config.
- Display page at `/` on the Pi backend. The phone app is NOT served by
  the Pi: in development it is Vite's dev server (`npm run dev` from
  `frontend/app/`, http://localhost:5173); in production it is the
  committed `frontend/app/dist/` behind Caddy at
  irin-out-of-sleep-at-hackgt.tech. The Device tab reaches the Pi
  through pairing (the device_url and token the relay hands back).
  `VITE_DEVICE_URL` in `.env.development.local` is a development-only
  override: when it is set and the backend's /api/health reports
  `hw: mock`, the Device tab uses it directly, skips pairing, and shows
  a small DEV badge. The Pi's own `DEVICE_URL` in .env is what it
  registers as device_url at pairing (https://device.<domain> on the
  Pi, http://localhost:8000 on a laptop).
- The relay and Irin Cloud run locally from `deploy/docker-compose.yml`
  (`docker compose up`: caddy, relay, cloud, plus local mongo and
  timescaledb containers standing in for Atlas and Tiger Cloud), or bare
  with `uvicorn main:app --port 8100 --reload` from `relay/` and `--port
  8200` from `cloud/`, with RELAY_URL and CLOUD_URL in .env pointing at
  them. The role pages are static: any static server over
  `frontend/clinician/`, `frontend/watch/`, and `frontend/family/`,
  pointed at the local services through their config.js; the app reads
  `VITE_RELAY_URL` and `VITE_CLOUD_URL` from `.env.development.local`
  instead. Only Chris needs Docker; everyone else points config.js and
  the VITE_ values at api.irin-out-of-sleep-at-hackgt.tech and
  cloud.irin-out-of-sleep-at-hackgt.tech. Whenever the services run on
  a laptop, the pages are served from that laptop too, on one origin: a
  page loaded over HTTPS cannot call an http:// service (browsers block
  it as mixed content). The venue fallback is exactly that arrangement,
  stated once in chris.md (R6 fallback); the pages and the app's dist
  do not work at http://<laptop-ip> unchanged.
- Narratives: `NARRATIVE_BACKEND=template` is the default on every laptop
  (no key needed; George's laptop stays on template); `backboard` and
  `anthropic` need their keys in .env. NARRATIVE_BACKEND names the FIRST
  link of narrative.py's chain (Backboard → Meta direct → direct
  Anthropic → template); provider `meta` is selected per task by
  `NARRATIVE_ROUTING`, never by NARRATIVE_BACKEND, and any routing row
  naming `meta` (or an `openrouter` row falling back while Backboard is
  down) needs `META_MODEL_API_KEY` in .env.
  Voice: `VOICE_BACKEND=none` renders nothing and uses the wav tones;
  `elevenlabs` needs its key and caches clips, never re-rendering the
  same text.
- Alarm, presence, ledger, pairing-expiry, lease, and treating timers read
  time from `app/clock.py` so replay speed accelerates them; never call
  time.time() directly in alarm, presence, scheduler, rounds, buddy, or
  relay-client code.
- The presence radar (GPIO17) is mocked under `IRIN_HW=mock` like the
  LEDs and speaker; presence transitions are testable on a laptop by driving
  the mock, no radar needed.
- `IRIN_BRAIN_ONLY=1` (or the demo-panel toggle) makes the Rounds
  adapter ignore presence, alarm hardware events, and logged context, so
  a Brain-only card can be shown next to a full one; its rows change
  confidence label (reported or inferred), they are never blanked.
- The replay datasource has a seek: `POST /api/demo/seek {step, day}`
  bulk-loads readings to that point, advances clock.py, and the engine's
  idempotent catch-up generates every card that should exist by then
  (a 24-week watch cannot be played: 4,032 hours ÷ 60 = 67 hours at
  60x). Seeking twice never duplicates a card.
- The Pi has no RTC battery, so its wall clock is untrusted until NTP
  syncs: staleness is monotonic time since the last new reading, and
  wall-clock jobs wait for the scheduler's sync guard (chris.md step 10).
  At the venue the hotspot goes up before the Pi. On the Pi, the backend
  runs as the kiosk user (systemd --user, linger on), never as root, so
  audio and the backlight work; RPi.GPIO and rpi_ws281x are never used.
- Python 3.14 everywhere: laptops natively (uv or pyenv), the Pi through
  uv (`uv python install 3.14`, `uv venv --python 3.14`), because
  Raspberry Pi OS ships 3.13 and not 3.14 from apt. Write 3.13-compatible
  code (no 3.14-only syntax) so the planned escape hatch works: if lgpio
  will not build into the 3.14 venv in the day-one proof, the Pi's
  backend runs on the system 3.13 with --system-site-packages and apt's
  GPIO packages (decision 19's venv, superseded by 27 as the default
  and kept only as this hatch), and nothing else changes. The Pi is aarch64 and the
  laptops are not; nothing compiled crosses that line. Every backend
  dependency must have a cp314 or abi3 aarch64 wheel or build from an
  sdist in that proof (backend/requirements.txt is the list; no pandas,
  scikit-learn, or local LLM on the Pi), the forecaster ships as XGBoost
  JSON and never as a pickle (*.pkl and *.joblib are never committed),
  and the stack is installed and tested on the Pi on day one (spec Phase
  2), not at the end.
- KIOSK AUTOSTART STAYS DISABLED until the whole project is complete and
  the final audit has passed. deploy/install.sh installs kiosk.service
  disabled; Claude NEVER runs `systemctl enable` on it or adds a kiosk
  line to any autostart file, no matter who asks. During development the
  display page is opened in a normal Chromium window, or by hand with
  `deploy/kiosk.sh --now` for a look at the real thing. Chris enables
  autostart by hand as the last step before judging. Even then the unit
  can never trap anyone: it refuses to start while /boot/firmware/NO_KIOSK
  exists (touch it from a tty via Ctrl+Alt+F2, over SSH, or from any
  computer with the SD card), it waits 20 s before Chromium appears, it
  never respawns (Restart=no), and SSH keeps working underneath it.

## journal/ — the cross-Claude bulletin

One file per LANE: journal/chris.md (backend, relay, shared files),
journal/justin.md (frontend), journal/george.md (ml), journal/slavik.md
(hardware, video). Journals belong to lanes, not people: whoever is
driving a lane writes that lane's file, so a lane's next session always
reads its own file. You OVERWRITE your lane's file entirely and never
edit another lane's; when you step in for a teammate, their lane's file is
yours for that session and its first line says so ("Updated: <time> by
Chris, stepping in for George"). Only one human drives a lane at a time,
so this never conflicts. Git preserves every past version
(git log -- journal/<name>.md), so the file is always current state and the
history costs nothing.

- FORMAT: first line "Updated: YYYY-MM-DD HH:MM", then exactly these
  headers, max ~20 lines total, a complete snapshot (not a delta):
  Done / In progress / Broken / Interface changes / Notes for other models.
  "Interface changes: none" is fine; a missing header is not.
- CONTENT RULE, from day one: journal entries never contain health details
  beyond what is already in the public pitch. "The Save replays clean" is
  fine; a real night's numbers, dates, or treatments are not.
- WRITE: at the end of every work session, immediately before any merge
  (feature into dev, or dev into main), and immediately before any
  compaction or /clear. Compaction
  tends to hit around 50% context, so do not wait for "low": once the
  context indicator passes ~50% used, checkpoint the journal at the next
  natural pause and after each completed step from then on, so a snapshot
  is always fresher than the squeeze. In every snapshot, "In progress"
  must name the exact next action (file, function, the failing test and
  its error), not a vague status, so a compacted or fresh session
  resumes without archaeology.
- AFTER a compaction: before continuing the task, re-read your own journal
  file and your docs/plans/<name>.md brief. The compacted summary is
  lossy; the journal is the ground truth for where you were.
- READ: at the start of every session, pull dev and read the other three
  journal files; re-read them any time you pull dev mid-session. During
  the hackathon itself, also re-pull and re-read every 2-3 hours or before
  starting a new feature.
- Your role's distilled plan lives in docs/plans/<name>.md (build order,
  interfaces, exact numbers, named checks, for core Irin AND the sponsor
  tiers). It is the Claude-facing version of the three spec documents, so
  don't paste any spec document into sessions.
  The standard session opener is: "Read docs/plans/<name>.md and the
  journal files, then: <today's deliverable>."
- git log stays the code history: commit messages must say WHAT changed and
  WHY — other Claudes read them too.

## One constitution for every agent

Claude Code reads this file automatically. Cursor does not, so
`.cursor/rules/irin.mdc` and `AGENTS.md` are generated copies of it
(`deploy/sync_rules.sh` regenerates both; never edit the copies). Any
agent that opens this repo, whichever tool or model, obeys the same
lanes, invariants, journal, speed rules, and main-is-human-only rule.
One agent per lane at a time; two agents on one lane means two humans
have not talked. Model selection inside Claude Code or Cursor is the
human's call; model selection inside the PRODUCT is narrative.py's
routing table and nothing else.

## Speed rules (binding during the hackathon)

- Nothing slower than ONE MINUTE runs inside Claude's loop. No CI. The
  only build step in the repo is the app's `npm run build` (seconds) and
  `npx tsc --noEmit`, both allowed in-loop for `frontend/app/`; everything
  else is verified by pytest (seconds) and a browser refresh. The ML
  pipeline runs in seconds, so Claude runs it in-loop; only a step slower
  than a minute (e.g. a hyperparameter search) is run by the human in a
  plain terminal and its output pasted back. Tests use clock.py's
  accelerated time — never sleep().
- Raw personal data (ml/data/) never enters Claude's context: scripts
  print summaries only, and Claude never opens or prints raw CSV rows.
- One deliverable per prompt. For non-trivial tasks, state your plan first
  and wait for approval before writing code.
- New feature, new session. Start fresh (/clear) between features; this file
  and the journal re-orient you in one read.
- A task spanning two owners' areas is split at the contract boundary and
  run by two Claudes in parallel, never serialized in one session.
- Sponsor-challenge work obeys the gates in "Build sequencing". Rounds
  and Night Buddy tasks name their step (R3, B2, ...) in the prompt; if
  the gate is not met, say so and stop.
- Model selection is handled by the team's agent-master tooling. Do not
  switch models on your own.
- Every task names its verification: a test to make pass or a concrete
  manual check. A task without a named check is not done when the code is
  written; it is done when the check passes.
- Stuck-loop cap: after ~15 minutes or three failed attempts at the same
  fix, STOP. Write the state to your journal file and escalate to the
  human. Do not keep circling.
- Big branches get /review (see .claude/commands/review.md) in a FRESH
  session before merging — the session that wrote the code never reviews
  it. Small single-file branches skip this.
- If a session burned unusual time on something (dependency fight, stuck
  loop, flaky tool), record it under "Notes for other models" in your
  journal so the next Claude does not repeat the excursion.

## Usage-limit hand-off

If a teammate hits their Claude usage limit mid-task, the WORK moves, not the
account. Two sessions on one machine are fine; the rules below keep them
from colliding.

Handing off (the teammate; no Claude needed, git works without it):
1. On the feature branch: `git add -A && git commit -m "WIP: <what>" &&
   git push`. Broken is fine.
2. If Claude was cut off before writing the journal, write
   journal/<lane>.md by hand: five headers, "In progress" naming the exact
   next action (file, function, failing test and its error).
3. Post branch name + that line in the group chat. Ping Chris, who takes
   it over.

Taking over (the taker's machine):
1. NEVER in the directory your own session uses. `git fetch`, then
   `git worktree add ../irin-<lane> <branch>`: the branch gets its own
   directory and the stepped-in session runs there. Gitignored files are
   not in a worktree: copy .env in, make or reuse a venv.
2. Ports: your own lane keeps 8000 (relay 8100, cloud 8200); the
   stepped-in worktree runs on 8001 (relay 8101, cloud 8201) via
   `--port` and its own .env RELAY_URL and CLOUD_URL, so both lanes run
   at once. The role pages' config.js and the app's VITE_ values name
   the ports, so the stepped-in session changes those too.
3. Open the session with "I'm stepping in on <lane> for <name>" (the
   Ownership exception), read that lane's plan and journal, and continue
   from the "In progress" line.
4. Plain commits only on a hand-off branch: never amend, rebase, or
   force-push, so the owner's `git pull` fast-forwards on return.

Handing back (when the owner's window resets):
1. The taker's session writes the lane journal ("... by Chris, stepping
   in for George"), commits, pushes, and stops. `git worktree remove
   ../irin-<lane>`.
2. The owner runs `git pull` on the branch and opens the normal session.

## Final audit

The night before judging, Chris runs `/audit` (defined in
`.claude/commands/audit.md`) in a FRESH session on a FRESH pull, so the
auditor hasn't seen the code being written. It covers backend/, relay/, and
every frontend page that exists by then. Findings go to `AUDIT.md`,
triaged fix-now vs mention-in-Q&A. Only after the fix-now list is empty
does Chris, by hand, enable kiosk autostart on the Pi (see Dev loop);
that is the last change before judging.
