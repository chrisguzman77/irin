# Decisions log and remaining to-dos (docs/OPEN_ITEMS.md)

Every question raised when the Rounds and Night Buddy specs were folded
into the workflow docs was answered by Chris on 2026-09-22. The answers
are now written into CLAUDE.md, the four plans, the three commands, the
spec, and docs/FAMILY_STORY.md; this file is the log, so nobody
relitigates them at 3 AM. Chris owns it; strike to-dos as they close.

## Decided (do not reopen)

1. **Family Story** is defined in docs/FAMILY_STORY.md and built as the
   F-steps in chris.md and justin.md, scheduled right after core Irin and
   before Rounds (about half a day of Chris, two hours of Justin).
   It is the Meta entry's built half. Invariant 19 covers it.
2. **Repository.** The working repo stays private. The Meta submission is
   a fresh public mirror built on submission day by Chris with
   deploy/public_mirror.sh: squashed history, journal/ and AUDIT.md
   excluded, ml/models/metrics.md trimmed to the headline numbers said on
   stage. Journal rule from day one: entries never contain health details
   beyond what is already in the public pitch.
3. **Hackathon rules.** Tracks and sponsor challenges are allowed; all
   three entries go ahead.
4. **Meta write-up** is a team effort: anyone drafts sections of
   docs/META_WRITEUP.md on a branch, Chris merges.
5. **Awareness-card honesty** (qualifier superseded by decision 26's line;
   the rule itself stands). The qualifier "glucose and near-misses
   real; wake and response data labeled" appears verbatim at the Act 1
   card beat in DEMO_SCRIPT.md and in the Q&A sheet; no unqualified
   "built from real nights" claim anywhere (the audit greps for it).
6. **Emergency script.** Demo tier: encrypted at rest on the relay,
   released only to the live claim-holder. Production answer, in the Q&A
   sheet: at claim time the relay notifies the device, which is awake
   during an episode by definition, and the device encrypts the script to
   the claim-holder's public key on demand, so it is end-to-end even to a
   volunteer unknown in advance.
7. **presence_during** comes from the raw radar, sampled every 30 s
   during an episode, aggregated once: "home" if a majority of samples
   show detection OR any detection within the 2 minutes around alarm
   start; "away" if zero detections; "unknown" otherwise (mock without a
   driven value, hal error, brain_only). Tests cover both flicker
   directions. Never PresenceState.mode.
8. **Kiosk PIN.** localStorage cache for one low-stakes kiosk verb: the
   on-screen acknowledge during an alarm (all morning questions live in
   the PWA, decision 21). Pairing confirm and doctor-message confirm
   re-prompt the touch keypad every time (require_fresh_pin; the list is
   FRESH_PIN_ENDPOINTS in contracts.py). The PWA keeps sessionStorage.
9. **on_transition hook** in alarm.py is core step 4, day one. It does
   not slip to a later step.
10. **Night Buddy watcher topology** (hosting superseded by 27): a static
    frontend/watch/ page, now at watch.irin-out-of-sleep-at-hackgt.tech
    on Vultr, paired as a browser peer like the doctor inbox; the
    mock-HAL laptop is for the video's both-devices scene.
11. **Contracts:** /skeleton had not run, so every Rounds, Night Buddy,
    and Family Story model is in the day-one contracts.py. R1 and B1 are
    fixture round-trips, not contracts commits.
12. **Invariants:** 22 in CLAUDE.md (6 core, 6 Rounds, 6 Night Buddy, 1
    Family Story, 3 for Irin Cloud, the app, and the sponsor
    integrations, decision 27); the audit checks every original rule
    from the challenge specs.
13. **nights.py is numpy-only**, so the Pi's backend needs no pandas.
14. **Companion JSON per scenario** (demo/scenarios/README.md) is the
    boundary between George's generator and Chris's batch ledger build.
15. **Demo-only panel controls** (list updated by decision 26: jump to
    step N day D added, "send card" for both programs): send awareness card (Bedside |
    Brain-only), brain_only toggle, simulate Spark offer, start watch,
    trigger buddy rung (which drives the mock radar first); all 404 in
    live mode.
16. **New sound buddy_chime**, optional LED states message_waiting and
    buddy_alert; MockHAL.get_presence() returns None until driven.
17. **Hardware review (2026-09-23).** Adopted into the spec's Phases 3,
    6, 7, 9, "Software on the Pi", slavik.md, chris.md, and deploy/:
    ground the six unused 74AHCT125 pins; the strip's data lead goes in
    tinned or via a Wago 221 (no soldering iron anywhere in the build);
    check the ALITOVE barrel fit; multimeter polarity check before first
    power (borrow a meter; the kit's LED + 330 ohm is the fallback); radar
    VCC from the 5V node if the display plug covers pin 4; GPIO17
    pull-down; gpiozero/lgpio on Pi 5 (never RPi.GPIO); screen blanking
    off; backlight udev rule; backend as the kiosk user's --user unit
    with linger (PipeWire); touch-rotation note; no RTC battery, so boot
    with the network up, staleness is monotonic, and the scheduler waits
    for NTP sync (clock_synced in the snapshot); sound files generated by
    backend/sounds/make_tones.py. Not adopted: the power-bank path (not
    planned; the Phase 9 battery test is marked optional), spares, and
    the touchscreen-acknowledge question (still open below).
18. **Kiosk autostart stays disabled until the project is complete and
    the final audit has passed** (2026-09-24). install.sh installs
    kiosk.service disabled; no Claude ever enables it; Chris enables it
    by hand as the last step before judging. The unit has escape
    hatches so it can never trap anyone at the terminal again: the
    /boot/firmware/NO_KIOSK flag (from a tty, SSH, or an SD card reader),
    a 20-second start delay, Restart=no, and SSH underneath. During the
    build the display page runs in a normal Chromium window;
    deploy/kiosk.sh --now shows the real kiosk on demand.
19. **Laptop-to-Pi architecture gap** (2026-09-24). No local LLM in this
    project (Claude API with pre-generated fallbacks), so last year's
    failure cannot repeat as such; the same class is guarded: 64-bit
    card verified with `uname -m` in Phase 2; the whole stack installed
    and pytest-ed on the Pi the day the skeleton lands; pytest on the Pi
    before every dev → main promotion; the model ships as XGBoost JSON,
    never a pickle; anthropic and matplotlib added to
    backend/requirements.txt (they were missing); laptop venvs match the
    Pi's Python; the Pi venv uses --system-site-packages for the apt
    GPIO libraries (superseded by 27: uv 3.14 venv; system 3.13 only as
    the escape hatch); sound via aplay/pw-play, no pygame.

20. **Hand-off mechanics** (2026-09-24). Two sessions on one machine are
    fine: the stepped-in lane runs in its own git worktree on its own
    ports (8001 / relay 8101 / cloud 8201, with its own RELAY_URL and
    CLOUD_URL; the role pages' config.js and the app's VITE_ values name
    the ports, so the stepped-in session changes those too); journals
    belong to lanes, not people, so
    the taker writes the lane's file and says so on its first line;
    hand-off branches get plain commits only (no amend, rebase, or
    force-push); ping Chris, who takes it over. CLAUDE.md "Usage-limit
    hand-off" has the step lists.
21. **Morning low-event recall** (2026-09-24; answer names and the LowEvent
    split updated by decision 26). Self-report is the
    clinical standard for hypoglycemia awareness, so Brain-only is not
    the weak version: the app asks about each specific nocturnal low the
    next morning with the curve attached (one card per event, at most
    two, four answers, carbs within 30 minutes of the nadir pre-fill
    treated, unanswered by noon is no_answer and never fine) and the card
    reports an unfelt-low rate = unfelt ÷ answered. Unfelt lows count
    from answers alone; the device adds exactly two rows (wake-and-
    response timing, presence confirmation). This departs from the Rounds
    spec's "counted only when presence confirmed". All input lives in
    the PWA; the kiosk shows a "questions waiting" chip and gains a big
    on-device acknowledge (ack_source records device vs phone), which
    settles the touchscreen-acknowledge question. Contract:
    LowEventRecall; SymptomCheck keeps only the stomach check.
22. **Hosting** (2026-09-24; superseded by 27: Vultr, Atlas, Tiger, the
    .tech domain, and the laptop compose fallback). The Pi hosts
    everything patient-side; the relay (cards, doctor messages, pairing,
    buddy alerts, hub) was first planned as one small FastAPI service
    with a file database on a PaaS, with the clinician inbox and watcher
    page as static pages on another host, talking only to the relay; 27
    moved all of it onto the Vultr box behind Caddy with the relay's
    documents on Atlas. What survives from this decision: the one
    fallback is everything on Chris's laptop, pages included, on one
    origin, because HTTPS pages cannot call an http:// relay (the
    statement is chris.md, R6 fallback). Spec "Deployment and Hosting"
    Layers 5 and 6; priority is tunnel, Nightscout, relay + inbox,
    family view.
23. **Participants without a device** (2026-09-24). A watcher (buddy or
    hub volunteer) needs only the watcher page and a CGM feed behind the
    account. A watched patient without a device runs the same backend in
    the cloud with the hardware flags off (IRIN_HW=mock, brain_only,
    DATASOURCE=nightscout) on the Vultr box (decision 27), appearing on
    the hub as unconfirmed. It is not deployed for the hackathon:
    Brain-only is demonstrated from the Pi's adapter with
    IRIN_BRAIN_ONLY=1 or the demo-panel toggle, at most as an optional
    second backend on a laptop, not new code; it could be one more
    container in the compose file later.
24. **The narrative** "A Day With Irin (Five Perspectives)" is in the
    spec: the sleeper, the doctor (with Impiricus's own products named at
    each beat: Ascend, Spark, Wallet, QPharma, Medvantx, Concierge, Ask
    an Endo), a second patient on Step Watch, the buddy and the hub, the
    mother, and Impiricus as the courier. Placeholder names: Dr. Patel,
    Maria and Dr. Alvarez, Tom in Melbourne, a volunteer in Lisbon.
25. **Per-use-case depth in the spec** (2026-09-24). Every section that
    breaks the device down now breaks Rounds, Night Buddy, and Family
    Story down the same way, so the main spec explains how all three
    work without opening the companion specs: sponsor terms in
    Terminology; six more data flows in How It All Works (the nightly
    ledger, the return path, pairing, the ladder, the hub, the family
    morning) plus the demo-mode batch build; one Tech Stack bullet per
    entry; what the sponsor tiers ask of the hardware; device
    Functional Requirements 13 to 15 (Rounds, Night Buddy, Family Story
    on the device); a Rounds Metrics section under Machine Learning
    (nights.py's three functions with the worked examples, the rules,
    labeling, threshold tuning, scenarios, metrics.md), ML requirements
    5 to 7, and LLM Morning Report renamed LLM Narratives (one module,
    four prompts, the validator and its inversion); the three phone-app
    bullets, Family Story in front of the family view, and two new Web
    App sections, Clinician Inbox (mock Ascend) and Watcher Page (Night
    Buddy), in the same data path / pages / access / setup / demo value
    shape as Family View; the Relay API v0 route list after the
    contracts table; the sponsor lists in /review; one SOP line per
    owner; four Demo Plan sections (the Rounds run of show in two acts,
    the Night Buddy and Family Story beats, the Meta video arc, sponsor
    logistics and fallbacks with the Q&A numbers); three packing items;
    and the growth loop, full tier, and Save Shared under Beyond the
    Hackathon. The plans and challenge specs stay the source of the
    numbers; the spec now carries them too, and where the Rounds spec
    and the plans differ (unfelt lows from answers alone, decision 21)
    the spec follows the plans.

26. **The updated Rounds spec is the source** (2026-09-25). The
    2026-09-25 Irin Rounds spec (Standing Cards + Step Watch, "two
    programs on one engine") supersedes the two-card brief the docs were
    built on, and every Rounds part of the doc set now follows it:
    CLAUDE.md (the project bullet, invariants 7-12, build sequencing and
    the cut order, the seek), chris.md (R1-R14 replace the R/S/U steps;
    Appendix A models verbatim plus two marked additions; the relay API;
    the numbers), george.md (steps 4-8: classify_night and the five
    nights.py functions, labeling with inferred reason codes, the three
    measured numbers with the leakage guard, basal-change windows and
    the synthetic titration), justin.md (kiosk glance surfaces, recall
    and check-ins, the Rounds tab, the confirm takeover, the two-layout
    inbox with the plan setup form, the demo panel's jump buttons),
    review.md, audit.md, skeleton.md (file list, contracts, tests, the
    scenario schema, DEMO_SCRIPT headers), the spec (One System bullet,
    Terminology, How It All Works, device requirement 13, the ML section,
    the phone app and inbox, contracts, the Rounds run of show, the
    narrative), and this log. Reconciliations made while folding it in,
    each reversible: (a) v2's models are adopted verbatim with two
    additions the rest of the project needs, is_demo on AlarmEvent,
    LowEvent, LowEventRecall, and Pairing, and peer_kind on Pairing for
    Night Buddy; (b) the typed-code pairing fallback is dropped from the
    hackathon build as v2 has it (pre-pair the demo phone instead;
    telehealth short codes are the production note); (c) the family view
    is KEPT despite v2's "recommended cut", because Family Story Level 2
    and hosting Layer 4 depend on it, with v2's mitigation adopted: it is
    never shown in the Impiricus slot, it reads Irin Cloud's rollup with
    a revocable family bearer (no Nightscout token anywhere in page
    source, decision 27), and it stays priority 4; (d) the Night Buddy gate is now
    "R1-R11 landed with a full day remaining", and the project cut order
    is Night Buddy code first, then v2's Rounds cut order (night replay
    first, never the confirm step, badges, banner, or noise budget); (e)
    the demo honesty line is now "glucose real; reason codes inferred and
    labeled; acknowledge, presence, and recall data are a labeled
    overlay"; (f) Ask an Endo is gone, as in v2; (g) the recall answer
    names are v2's (felt_and_treated, woke_no_symptoms, dont_remember,
    was_awake); (h) the narrative's doctor and Step Watch sections now
    tell v2's story (Basal Check → confirm → Follow-up → Hypo Response
    and the glucagon moment; Maria's full watch to graduation), which
    settles v2's open item 7 with the main spec as the one version.

27. **The hosted app, Irin Cloud, and the six sponsor integrations**
    (2026-09-25 evening, hacking underway). Python is 3.14 everywhere,
    the Pi through uv, code kept 3.13-compatible as the escape hatch. One
    hosted app at irin-out-of-sleep-at-hackgt.tech (registered Friday
    night, Active on Cloudflare) with
    four tabs, Irin Device, My Irin, Irin Buddy, Irin Rounds, gated by
    the PIN with no login; the kiosk keeps only glance-and-alarm screens.
    Pairing your Irin is the kiosk's QR and 6-digit code (single use, 10
    minutes, shown only while the radar sees someone), not Bluetooth.
    Everything Irin owns in the cloud runs on one Vultr VPS from
    deploy/docker-compose.yml behind Caddy with automatic HTTPS: the
    relay (courier, hub, buddy directory and matching; MongoDB Atlas;
    never a glucose value) and Irin Cloud (cloud/: the Pi's outbound
    forwarder lands in Tiger Cloud hypertables, continuous aggregates
    draw the eleven My Irin dashboards and the family rollup, ElevenLabs
    audio is rendered and cached). Nightscout stays on Railway with its
    database on Atlas. The family page reads the cloud rollup with a
    revocable family bearer, so no Nightscout token sits in page source
    (which answers the Rounds spec's objection and keeps the page).
    ElevenLabs is the voice (the buddy alert loop, the device's spoken
    echoes after the tone, the morning report's play button, Mom's
    voice note, the script read to a volunteer). Backboard is the
    narrative backend with per-scope memory, a routing table, and a
    second-opinion model, behind the validator, with the direct Claude
    call and the templates as the fallback chain (the chain gains the
    Meta Model API as its second link in decision 29). Cursor runs beside
    Claude Code under the same constitution (.cursor/rules/irin.mdc and
    AGENTS.md are generated from CLAUDE.md). Find a Buddy is the signup
    flow: opt in, known by username or matched, the political-timezone
    globe (d3-geo orthographic over timezone-boundary-builder shapes,
    live clocks, multi-select; 24 offset bands as the fallback),
    languages, availability with 22:00 to 08:00 locked, the emergency
    contact encrypted and brokered; matching is one Atlas aggregation.
    New invariants 20-22 in CLAUDE.md. The laptop fallback is the same
    compose file on Chris's laptop with local mongo and timescaledb,
    serving everything on one origin, phones on the hotspot opening
    http://<laptop-ip> (the one statement is chris.md, R6 fallback).
    Locked-phone honesty: a web page cannot ring through silent; the
    production answers are a native app with Critical Alerts or a call
    (from a number saved with Emergency Bypass, or the WhatsApp/Twilio
    calling API), and the demo keeps the watcher page open with a wake
    lock and sends the WhatsApp message as the second channel (decision
    29).
28. **The hosted app is Vite + React + TypeScript + Tailwind**
    (2026-09-25, late evening). frontend/app/ only; display/ and the
    three role pages stay plain HTML with no build step. Source in
    app/src/ (one folder per tab, shared hooks usePin and
    useDeviceSocket, an api client that adds the X-PIN header), styling
    in Tailwind, charts and the globe as d3 inside components. `npm run
    build` writes app/dist/, which is COMMITTED and is what Caddy
    serves, so the Vultr box never runs Node; Justin rebuilds and
    commits dist/ before every deploy and the Caddyfile gains try_files
    to index.html. Config: src/config.ts reads VITE_RELAY_URL and
    VITE_CLOUD_URL; .env.production (committed, public URLs) is what the
    build bakes in, .env.development.local (ignored) points at
    localhost. Types are generated, not typed: `npm run types` runs
    openapi-typescript against the Pi's and the relay's /openapi.json,
    so "never invent a field" is a compile error. d3 and the timezone
    GeoJSON are bundled (the no-CDN rule holds). Node 22 LTS is
    installed by Justin only; the Pi backend adds CORS for
    http://localhost:5173. Speed rule carve-out in CLAUDE.md: the build
    and tsc run in seconds and are allowed in-loop; adding a dependency
    is Justin's call, never an agent's. /skeleton writes the scaffold by
    hand plus a placeholder dist/index.html so the domain answers from
    the first deploy. Reason: componentized tabs, typed contracts for
    the globe and the availability grid, and Tailwind instead of a
    hand-written stylesheet, for one build step that takes seconds.
29. **Meta: the Model API, Muse Spark, and the WhatsApp channel**
    (2026-09-26, after midnight). The Meta challenge entry is built on
    Meta's own pieces. The Meta Model API (https://api.meta.ai/v1,
    OpenAI-SDK compatible: the `openai` package with base_url; key
    META_MODEL_API_KEY on the Pi AND on the server; models
    muse-spark-1.3 for text and structured output,
    muse-voice-transcribe-1.0 optional later for voice logging,
    muse-image-1.0; `openai` goes into backend/ and relay/
    requirements). narrative.py's chain becomes FOUR links, Backboard →
    Meta direct (when the routing row names provider meta, or as the
    fallback for openrouter rows while Backboard is down) → direct
    Anthropic → the deterministic template, the no-invented-numbers
    validator after every link. NARRATIVE_BACKEND keeps backboard |
    anthropic | template and names the FIRST link; provider meta is
    chosen per task by NARRATIVE_ROUTING (JSON, task → [provider,
    model]), which has EIGHT final task keys: clinician_card and
    morning_report → anthropic / a Sonnet-class Claude; buddy_line and
    family_story → openrouter / the Muse Spark name if Backboard's
    OpenRouter provider carries it, else meta / muse-spark-1.3;
    match_explanation, emergency_script, availability_parse → meta /
    muse-spark-1.3 (always direct, structured output); second_opinion →
    openai / a small model (a different provider from the writer). What
    Muse writes in Irin Buddy: the introduction line and the
    why-this-match line (from the score parts; first names only, never
    a glucose value), the emergency script (rough notes → a calm ordered
    script, JSON fields), free-text availability parsed into the
    week-grid rows the user then confirms (the deterministic scorer uses
    the rows), and the Family Story. The MATCHING SCORE STAYS
    DETERMINISTIC (the Atlas aggregation); an LLM never picks the buddy.
    Graph API: the WhatsApp Cloud API is the second alert channel. At
    the buddy rung the relay (relay/notify.py, new) sends the registered
    buddy a WhatsApp text ("Irin: your buddy <first name> is in trouble.
    The alarm has been unacknowledged for N minutes. Open <watch URL>")
    and the ElevenLabs clip as an audio message (type audio, link on
    cloud.<domain>) via POST
    https://graph.facebook.com/<version>/{PHONE_NUMBER_ID}/messages,
    alongside the watcher-page loop; never a glucose value in the
    message (relay invariant); free-form messages only inside WhatsApp's
    24-hour window after the buddy last messaged the number (each buddy
    sends "hi" during setup and before the demo), outside it an approved
    template; env on the server only: WHATSAPP_TOKEN,
    WHATSAPP_PHONE_NUMBER_ID. Locked-phone honesty: demo = wake-locked
    watcher page + WhatsApp; production = native Critical Alerts or the
    WhatsApp/Twilio calling API. Optional Facebook Login for "a buddy I
    already know": VITE_FB_APP_ID in frontend/app/.env.production
    (public), /me/friends returns friends who also use Irin; only if
    Justin has time; cut first. .env.example gains META_MODEL_API_KEY,
    WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID (server), and
    frontend/app/.env.example gains VITE_FB_APP_ID. Safety: WhatsApp
    payloads and Muse prompts never carry a glucose value; /review and
    /audit grep relay/notify.py and the buddy prompts for mgdl. Written
    into CLAUDE.md (invariant 22, the dev loop, never-commit), chris.md
    (R13+, B3+, B4+, I5), justin.md (A5, A6), george.md (the
    second-opinion prompt pointer), review.md, audit.md, skeleton.md,
    the spec, and the setup docs.
30. **Settled the same night, folded into the plans** (2026-09-26).
    The domain is registered: irin-out-of-sleep-at-hackgt.tech (Active
    on Cloudflare; six A records @, api, cloud, doctor, watch, family →
    the Vultr IP, all DNS-only; device. comes from the tunnel). Owner
    pairing goes through the relay: the Pi registers the kiosk code
    with POST /v0/device/pairings, the app redeems it with POST
    /v0/device/pair {code, username} → {device_id, device_url, token},
    the Pi confirms on its next poll, DELETE /v0/device/pair unpairs;
    DevicePairing is its own model and Pairing.peer_kind stays doctor |
    buddy (chris.md R5+, the Relay API list, justin.md A2). The Pi's
    .env gains DEVICE_URL; the app's VITE_DEVICE_URL is a
    development-only override honored only while /api/health reports
    hw: mock (CLAUDE.md and justin.md dev loops, skeleton config). The
    laptop fallback is one origin, http://<laptop-ip>, from the same
    compose file, the app's dist rebuilt for it; stated once in
    chris.md R6 and cited everywhere else. The Brain-only backend is
    not deployed this weekend (demonstrated from the Pi's adapter with
    IRIN_BRAIN_ONLY=1 or the demo-panel toggle). George's laptop runs
    NARRATIVE_BACKEND=template. Raspberry Pi OS is Trixie (Debian 13,
    64-bit) wherever the release is named. Nightscout's Admin Tools
    tokens live in its database, so the move to Atlas recreates the
    `irin` read token or restores the old data (chris.md I3).

## Hour one, the event has started (in order)

0. Chris, first: the seven accounts and every key in one private note
   that becomes the team's .env: the .tech domain (DONE Friday night:
   irin-out-of-sleep-at-hackgt.tech, nameservers on a free Cloudflare
   account, which the Pi's named tunnel needs anyway; the six A records
   for irin-out-of-sleep-at-hackgt.tech, api., cloud., doctor., watch., family. to the Vultr IP
   are live, all DNS-only; device. comes from the tunnel), Vultr (DONE:
   one Ubuntu 24.04 VPS in Atlanta with Docker, ports 80 and 443),
   MongoDB Atlas (M0, a user, open network access for the weekend; set
   Nightscout's MONGODB_URI on Railway to it and recreate the `irin`
   read token, chris.md I3), Tiger Cloud (a free service, Toolkit on),
   ElevenLabs (key, two voice ids), Backboard (key, the Anthropic model
   names from /api/models), the Meta developer app (META_MODEL_API_KEY,
   the WhatsApp test number with WHATSAPP_TOKEN and
   WHATSAPP_PHONE_NUMBER_ID, up to five registered recipients; decision
   29). Plus the ones already held: Railway (Nightscout only),
   Cloudflare, Anthropic, SMTP, the PIN, the relay source key.
1. Chris: run /skeleton, make the initial commit, create dev, turn on
   branch protection, send invites; then docker compose up on the Vultr
   box so api.irin-out-of-sleep-at-hackgt.tech and
   irin-out-of-sleep-at-hackgt.tech answer over HTTPS before anyone
   writes a line against them (irin-out-of-sleep-at-hackgt.tech shows
   the skeleton's placeholder page until Justin's first build). Nothing
   else can start
   before this.
1b. Justin, right after cloning: Node 22 LTS, `npm install`, `npm run
   types`, `npm run build`, commit dist/ (decision 28); Chris pulls and
   `docker compose up -d` so the real app replaces the placeholder.
2. Everyone: the "Initial Setup, Per Person" steps from the spec,
   including the first journal entry and branch, and `claude update`;
   confirm the custom commands load.
3. Chris + Slavik: flash the card, Phase 2 bench boot, and the day-one
   stack proof on the Pi (uname -m, uv Python 3.14, the two requirements
   files, ffmpeg, pytest, mock backend; fifteen minutes on lgpio, then the
   3.13 escape hatch); pre-add the hotspot and test SSH over it; the
   named tunnel at device.irin-out-of-sleep-at-hackgt.tech.
4. Chris: Dexcom CSVs to George on a stick; George runs cleaning and
   baselines if pre-work is allowed. NEW AND REQUIRED for the Rounds demo:
   Chris lists 2 to 3 real basal increases (dates, with confidence
   notes) in gitignored ml/data/therapy_changes.txt, away from the two
   Libre holes; the detect / decide / verify beats replay the real nights
   around one of them, and the lead-time number comes from all of them.
5. Chris: the rest of the infrastructure: Nightscout on Railway fed by
   Dexcom Share and readable from the Pi (already done; only the Atlas
   URI changes); SMTP tested with one real email; George's SQL applied
   to Tiger Cloud once C2 lands; the 22 months loaded (C5).
6. Chris: the rules questions still open: what pre-work is allowed
   (skeleton commit, hardware phases, ML pipeline), the submission
   format and deadline for each entry (the public mirror must exist
   before the Meta deadline, not before judging), and the judging slot
   length (the run of show assumes 3 minutes).
7. Team: agree the weekend checkpoint: The Save end to end by Saturday
   early afternoon, or feature work freezes and the rest of the weekend
   is core polish, the video, and the write-up.
8. Slavik: hardware phases as far as the parts and the rules allow;
   the video storyboard on paper.

## Still to do (owner)

- Chris: confirm the LIELONGREN shows up in `aplay -l` as a USB audio
  device; if it is a 3.5 mm-input soundbar, a USB sound dongle is the
  fix.
- Chris: the domain name is DONE (irin-out-of-sleep-at-hackgt.tech,
  recorded in every doc); still open: whether to restore the typed-code
  pairing fallback for the doctor (cheap, one input box). The family
  view stays (decision 27) and the Tech Stack's four stale bullets are
  fixed.

- Chris: answer the recall question for a few real nights before or
  during the event; paste the Rounds Q&A ammunition and the Night Buddy
  Q&A table into DEMO_SCRIPT.md at R13; run deploy/public_mirror.sh on
  submission day.
- George: the basal-change windows with their labeled overlay (at least
  one recall answer left as no answer); the three measured numbers; the
  qualifier and the headline block in metrics.md.
- Team, at the event: rehearse the laptop fallback (chris.md R6) once
  before judging; any consenting GLP-1 volunteer's date-shifted data
  beats the synthetic scenario; Twilio only if the full tier becomes
  plausible; each registered buddy sends "hi" to the WhatsApp test
  number before the demo (the 24-hour window) and the message is
  checked on the team's actual phones before the video narration
  promises it; pick and pre-provision the buddy demo phone.
- Everyone: the write-up sections of docs/META_WRITEUP.md.
- Slavik: film the dark-room scene early enough for a re-shoot; add the
  20-second Family Story split shot.
