# Justin — Frontend plan (docs/plans/justin.md)

Read this at the start of any session working frontend/. CLAUDE.md has
the repo rules; this file is WHAT to build and in what order.

## Lane

Owns frontend/: display/ (kiosk page, served by the Pi's FastAPI at /),
app/ (the hosted four-tab app at irin-out-of-sleep-at-hackgt.tech: Irin
Device, My Irin, Irin Buddy, Irin Rounds), family/
(family.irin-out-of-sleep-at-hackgt.tech, reads Irin Cloud's rollup),
clinician/ (the doctor inbox, "mock Ascend",
doctor.irin-out-of-sleep-at-hackgt.tech, talks only to the relay),
watch/ (the buddy and hub watcher page,
watch.irin-out-of-sleep-at-hackgt.tech, talks to the relay and plays
Irin Cloud's audio). app/
and the role pages are served by the compose file's Caddy; the Device
tab talks to the paired Pi through its tunnel.

Two kinds of page (decision 28). app/ is a Vite + React + TypeScript +
Tailwind project: source in app/src/, one component folder per tab,
`npm run build` writes app/dist/, and dist/ is COMMITTED because Caddy
serves it and the server never runs Node. Rebuild and commit dist/
before every deploy; a stale dist/ is the one way a merged feature
fails to show up. display/, clinician/, watch/, and family/ stay plain
HTML/CSS/JS with no build step (one screen each; the watcher page's
wake lock and audio loop are simpler without a framework): edit, save,
refresh. Charts are d3 or canvas inside components (no chart
framework); the globe is d3-geo owning a canvas inside a useEffect.
Adding an npm dependency is your decision, not the agent's; keep the
list short (react, react-dom, d3 and the dev tooling).

Contracts are typed, not remembered: `npm run types` regenerates
app/src/types/pi.d.ts and relay.d.ts from the Pi backend's and the
relay's /openapi.json (openapi-typescript). Re-run it whenever Chris
changes contracts.py; a field that does not exist becomes a compile
error instead of a 3 a.m. surprise. Chris drafts the useDeviceSocket
hook on a branch against contracts.py; you own it after merge. Chris
owns the voice confirm logic.
Rule: changes to how things LOOK need nobody's sign-off; changes to WHAT
MESSAGES ARE SENT to the backend or the relay need Chris's sign-off
before merging. Message shapes come from backend/app/contracts.py and
relay/README.md, through the generated types; never invent a field.
Every new button in Rounds and Night Buddy is a new mutating endpoint,
so it is contract work first, UI second.

## Dev loop

Install once: Node 22 LTS (you are the only one on the team who needs
it), then from frontend/app/: `npm install`, `npm run types` (with the
mock backend and the relay running, or against api.irin-out-of-sleep-at-hackgt.tech), and
`npm run dev` → http://localhost:5173 with hot reload.
frontend/app/.env.development.local (ignored by git) holds
VITE_RELAY_URL and VITE_CLOUD_URL for local services and
VITE_DEVICE_URL=http://localhost:8000, the development-only override
for the Device tab: when it is set and the backend's /api/health
reports hw: mock, the Device tab uses it directly, skips pairing, and
shows a small DEV badge (a real HAL ignores it; the Pi registers its
own DEVICE_URL at pairing, A2). .env.production (committed) holds the
hosted URLs and is what `npm run build` bakes in. Run the backend
locally (IRIN_HW=mock uvicorn
app.main:app --reload from backend/, replay datasource selected; install
backend/requirements.txt once). Display at localhost:8000/; the app is
never served by the Pi. For anything beyond the Device tab, use
api.irin-out-of-sleep-at-hackgt.tech and
cloud.irin-out-of-sleep-at-hackgt.tech (Chris's box is up from hour
one), or, when Chris runs the compose file locally, his laptop. Serve
clinician/,
watch/, and family/ through any static server (python -m http.server
from the folder). Iterate with Claude by screenshot: paste what the
screen looks like and say what is wrong. Test the app on a real phone
(`npm run dev -- --host`, then the laptop IP on the same Wi-Fi) at least
once per feature; the phone is what judges hold. Before every merge:
`npx tsc --noEmit` clean and `npm run build` green, then commit dist/
with the feature. Test the inbox and the QR pairing on EVERY phone the
team owns before judging.

## The hard client rules (apply to every page)

1. Pages that talk to the Pi (display, app) render only from the
   state_snapshot the backend sends on every WebSocket connect, plus
   subsequent updates. Never assume the page saw messages while
   disconnected. On reconnect (including phone unlock), the snapshot
   arrives first and the page repaints from it. Socket down more than
   ~15 s → show a disconnected banner (same honesty rule as stale data).
   While the snapshot's clock_synced is false (the Pi has no RTC
   battery and has not synced yet), both pages show a small "clock not
   set" badge; it clears on its own.
2. The PIN is entered once, kept in memory or sessionStorage on the phone,
   and sent as a request header on every mutating call. Never put it in
   a URL. The kiosk display stays glanceable: every question and form
   lives in the PWA. The kiosk's one LOW-stakes verb is the on-screen
   acknowledge during an alarm, which uses a PIN entered once on a touch
   keypad and kept in localStorage on the Pi's Chromium, a kiosk-only
   concession. HIGH-stakes kiosk verbs, pairing confirm and
   doctor-message confirm, ALWAYS re-prompt the keypad, cached PIN or
   not; the list is FRESH_PIN_ENDPOINTS in contracts.py, read by
   display.js and by the app's usePin hook, never hand-copied. No other
   auth path exists.
3. Pages that talk to the relay (clinician, watch) hold their private key
   in localStorage, authenticate with the bearer issued at pairing, and
   never render a card or alert they cannot decrypt and authenticate. The
   DEMO badge comes from the envelope's is_demo and is unmissable. On
   revoke from either side they show "sharing ended". They have no PIN
   (their verbs are authenticated by the key and bearer) and never talk
   to the Pi.

## Build order — tier 1, core (each step names its check)

1. **Display detail screen:** big number, trend arrow, 3-hour graph with
   target band and DOTTED forecast line, IOB, minutes since last dose,
   today's TIR. Check: replay scenario renders live over the WebSocket.
2. **Display modes:** Detail (day), Night (dim clock, smaller number,
   during the configured night window), Morning (overnight low/high with
   times, time below/above range, TIR rings, for 2 h after night window
   ends). Check: mode switches driven by backend state, not local time.
3. **Alarm visuals, two tiers, visually distinct always:** predicted-low
   WARNING = amber ramp; actual-low FULL = red bright pulse, strobe on
   escalation; stale = banner + grey number; high = amber tint +
   persistent indicator, no ack UI. The tier comes from the alarm
   state's trigger_type field in contracts.py. The full-screen alarm
   takeover on the kiosk carries a large Acknowledge button (a phone
   across the room at 2 AM is the failure the device exists to prevent);
   it uses the kiosk's cached PIN and sends ack_source = device, so the
   response-time metric records which screen answered. Check: replay The
   Save shows amber first, then red on escalation, and a tap on the kiosk
   silences it within a second.
4. **PWA live view + acknowledge button:** full width at top, visible
   only while an alarm is active, silences within 1 second via WS
   message, works after phone lock/unlock (reconnect on focus).
   Check: lock the phone mid-alarm, unlock, ack still works.
5. **PWA settings forms:** thresholds, predictive on/off + lead time,
   high-alert behavior (one-shot default, optional still-high reminder
   hours), night window, LED colors per state (warning and full tiers
   must stay visually distinct), sound picker with preview, volume,
   email, basal time, Home/Away presence override (radar is automatic;
   the toggle is the manual fallback that ALWAYS wins). Check: a change
   applies without restart and survives reload.
6. **Logging UI + voice button:** basal taken, carbs + units, notes.
   Mic button uses the browser Web Speech API; recognized text goes to
   the backend parser; the ECHO + CONFIRM screen is sacred: insulin is
   never saved without explicit confirm (backend enforces it; the UI
   must never bypass or auto-confirm). Check: a partial parse asks for
   the missing piece.
7. **Reports tab:** stored morning reports by date.
8. **Demo panel (the Demo section of the Device tab, an in-app route,
   /demo):** reached by a BUTTON in the app,
   never by typing the URL: a small, low-key entry (an icon at the
   nav's edge or on the settings screen), one tap to the panel, so at
   the demo table the whole gesture is app → panel → switch, two taps.
   Keep it out of the main tab row; it is stagecraft, not a daily
   screen. On the panel: the LIVE/DEMO mode switch at the
   top (one tap, PIN-gated, drives the backend's /api/mode), then
   scenario picker, speed slider, pause feed, inject low, basal-time
   button, and the sponsor controls as they land (jump to step N day D,
   send card Bedside | Brain-only, brain_only toggle, simulate Spark
   offer, start watch, trigger buddy rung). Everything below the switch only works in
   demo mode (backend 404s in live); grey those controls out in live mode
   rather than let them look broken. While demo mode is active, EVERY
   screen (display, PWA, inbox, watcher) shows an unmistakable DEMO badge
   driven by the mode_change WS message or the envelope flag — same
   honesty rule as the stale indicator, and it must be impossible to
   miss.
9. **Stretch: family view (family/):** single static page at
   family.irin-out-of-sleep-at-hackgt.tech, fetching GET /v1/family/last_night from Irin Cloud
   with a family bearer (A3; no Nightscout token in page source any
   more). Shows current value + trend arrow, minutes since reading, and
   the last-night strip the rollup returns. Refresh every 60 s; if the
   newest reading is older than 15 min, grey the number and say stale.
   No PIN because the page has no verbs; the bearer is revocable from the
   app's Family section. Check: page loads with the Pi off (the rollup
   lives in the cloud) and says stale. The page itself is unchanged by
   Family Story; the stretch story card (F3) sits above it.

## Build order — the hosted app (A-steps; the gate, the tabs, pairing, My Irin, Find a Buddy)

Decided 2026-09-25 evening. The phone app is no longer served by the Pi:
frontend/app/ is ONE hosted app at irin-out-of-sleep-at-hackgt.tech
with four tabs across the top, Irin Device, My Irin, Irin Buddy, Irin
Rounds. There
is no login. The Device tab reaches the Pi through its tunnel after
pairing; the other tabs and the role pages talk to the relay
(api.irin-out-of-sleep-at-hackgt.tech) and Irin Cloud. A1-A3 land with the core; A4 (My Irin)
runs in parallel with Rounds once C3 exists; A5 (Find a Buddy) lands
with the Night Buddy tier or, if that tier is cut, as the video's signup
scene. Charts use plain canvas or d3 inside React components (no chart
framework); the globe uses d3-geo. Each A-step is a folder under
app/src/tabs/ (device/, myirin/, buddy/, rounds/) plus shared hooks in
app/src/lib/ (usePin, useDeviceSocket, api client with the X-PIN
header).

A1. **The gate and the shell.** First load shows one input: the code
    ("1234", the backend PIN). It is kept in sessionStorage (a usePin
    hook) and sent as the X-PIN header on every mutating call to the Pi
    and to the relay; a wrong code gets a 401 and the input again. Four
    tabs as React routes or a tab state, the active one remembered in
    localStorage, every screen rendering from state_snapshot plus
    updates (the hard client rule) through one useDeviceSocket hook that
    owns the socket and exposes the latest snapshot. Tailwind for all
    styling; no CSS files beyond index.css. Check: a reload returns to
    the same tab with the code still in the session; closing the tab
    forgets it; `npm run build` produces a dist/ that Caddy serves at
    the domain with the same behavior.
A2. **Pair your Irin (Device tab).** The first button when no device is
    paired: "Look at your Irin" — the kiosk shows a QR and a 6-digit code
    (display.js: a screen driven by pairing_state, shown only while the
    radar sees someone). The app scans (BarcodeDetector where available,
    a file input fallback) or takes the typed code (the QR encodes the
    same code plus the app URL, so scanning equals typing), POSTs
    `{code, username}` to the relay's POST /v0/device/pair, stores
    device_url and token from the reply `{device_id, device_url, token}`,
    and switches to the live view within a second (the Pi confirms on
    its next relay poll). Unpair lives in settings (DELETE
    /v0/device/pair with the token; the Pi revokes on its next poll).
    Every existing Device-tab feature (live view, acknowledge, settings,
    logging, voice logging, reports, the demo panel) now calls
    device_url with the token and the PIN. Check: a fresh phone pairs in
    under a minute on the hotspot; a wrong code is refused; the live
    number updates over the tunnel from cellular.
A3. **Role pages on the same box.** family/, clinician/, and watch/ stay
    separate folders, served by the compose file's Caddy at family.,
    doctor., and watch.irin-out-of-sleep-at-hackgt.tech; their config.js
    points at api.irin-out-of-sleep-at-hackgt.tech and
    cloud.irin-out-of-sleep-at-hackgt.tech. family/ now reads GET
    /v1/family/last_night with the family
    bearer instead of Nightscout (the token in page source is gone). Check:
    every page loads over HTTPS from a phone on cellular.
A4. **My Irin tab (C4).** Eleven charts, each one fetch of
    /v1/dash/{name}?days= with the owner bearer and one draw: nights strip
    (tiles colored by reason code, tap for the night's numbers), time in
    range trend (stacked daily bars with 7- and 30-day lines), overnight
    profile (median line with 10-90 band by time of day), where my lows
    live (hour × weekday heatmap), alarm response (bars per night by tier,
    median-minutes line), near-misses per week, basal timing (dots against
    the usual-time line, plus the clean-night rise), sensor health
    (coverage bars with gap minutes), Step Watch (low point against the
    baseline band, tolerance strip, adherence dots; hidden when no watch),
    buddy (nights covered, streak, alerts), under the hood (readings
    stored, compressed size, ratio, last sync). Every chart shows "as of"
    and a DEMO badge when the data is the demo device. Check: the page
    renders from the seeded night in C3's tests and from Chris's 22 months
    after C5; a chart with no data says so instead of drawing zeros.
A5. **Find a Buddy (Irin Buddy tab).** One screen per step, all input in
    the app, POSTed as one UserProfile to /v0/users at the end: (1) opt in
    to a personal buddy; (2) someone you know, a username search over
    /v0/users/search, or match me; (3) the globe: a d3-geo orthographic
    projection on a canvas inside a Globe component (d3 draws in a
    useEffect; React only holds the selection state), drag to rotate
    with inertia, the political timezone under the pointer highlighted
    as it turns with its live clock (Intl.DateTimeFormat with the zone's
    IANA name), tap to select or unselect, multi-select, your own zone
    pre-selected; shapes from the timezone-boundary-builder GeoJSON
    simplified with mapshaper to a few MB, imported from app/src/assets/
    so Vite bundles it (never a CDN at demo time); fallback if it fights
    back: 24 UTC-offset bands with the same interaction; (4) languages, a chip
    picker; (5) availability, a week grid of hours with 22:00 to 08:00
    locked as asleep and not uncheckable; it can also be typed as free
    text ("weeknights after nine, all day Sunday"), which the relay
    parses (Muse, task availability_parse) into grid rows the user then
    confirms on the same grid before anything is stored; only the
    confirmed grid is sent; (6) the emergency contact: the number (sent
    once, encrypted by the relay, never displayed again, shown as "on
    file") and the instructions as rough notes; the relay returns the
    Muse-written calm, ordered script as text for the user to read and
    confirm. Then the matches screen (top three: first name, timezone,
    shared languages, hours covered, mirror or twin, and the
    why-this-match line, which arrives from the relay as text, written
    by Muse from the score parts, first names only), accept or decline,
    and pairing. The app never calls Meta directly: every Muse-written
    line is relay text (decision 29), and the match order itself is the
    relay's deterministic score. Check: the profile round-trips through
    the relay; the globe selects and unselects on a real phone; the
    sleep hours cannot be unchecked; a typed availability lands on the
    grid for confirmation and nothing is stored before the tap; the
    number never appears anywhere after submission.
A6. **The watcher's voice (watch/).** When the buddy taps On watch, the
    page requests a screen wake lock and unlocks audio with that gesture;
    on buddy_alert it loops audio_url (the ElevenLabs clip) through Web
    Audio until Call or I've got this is tapped, then stops. The relay
    also sends the same text and clip as a WhatsApp message (decision
    29), the second channel; it cannot ring through a silenced phone
    either, so the caveat stays on the screen in small type: "a locked
    phone needs a native Critical Alert or a call; keep this page open
    while on watch." Check: the loop starts within a second of the alert
    on a real phone and stops on Call; the WhatsApp message lands on the
    same phone.

## Build order — Family Story (F-steps; right after core, about two hours)

Spec: docs/FAMILY_STORY.md. Chris builds the backend half first (his
F1-F3); these are the screens.

F1. **Recipient settings (PWA).** A "Family" section in Settings: add a
    recipient by name and email, pick the level (Story only / Story +
    view) and the send mode (automatic / approve each morning), with
    Pause and Revoke per recipient, all through the PIN-gated endpoints.
    The default is shown plainly: the first story needs approval, then
    automatic. Check: a change survives reload and shows in the snapshot.
F2. **Morning chip + approval.** On the morning screen and the app home:
    "Sent to Mom" (one chip per recipient sent, from family_story_status
    in the snapshot and family_story_sent), with one-tap Pause per
    recipient; when a story is pending approval (family_story_pending),
    show the story text with Send and Skip (PIN). Demo-mode stories carry
    the DEMO badge and say "not sent (demo)". Check: the video beat's
    morning shot ("Sent to Mom", the pause button) renders from a replay.
F3. **Stretch: story card on family/.** Only after the relay exists: a card
    at the top of the family page, fetched from the relay with a bearer,
    showing the story above the unchanged read-only view. Check: the
    page still works with the Pi off, with or without the card.

## Build order — Rounds (gate: core runs end to end)

Source: the updated Rounds spec (docs/Irin_Rounds.pdf, 2026-09-25). Two
programs, Standing Cards and Step Watch, one SignalCard model, one
inbox. The split that governs every screen: the phone app carries ALL
input; the bedside display glances and alarms and carries no forms.
Chris builds the backend half first (his R-steps); the order below
follows his, so each screen has data the day it is needed.

R1. **Kiosk chip, physical acknowledge, QR screen.** The display gains
    exactly this from Rounds: a passive chip ("2 questions waiting in the
    app") from todays_checkin_status; the big on-device Acknowledge
    during an alarm (the one verb that may use the cached PIN; the
    backend records ack_source = device); the "Share with my doctor" QR
    screen reached from the settings glance, showing the QR, then the
    four-digit code once the doctor side has joined, and a Confirm that
    re-prompts the keypad every time (fresh PIN; states from
    pairing_state: pending / paired / revoked); the doctor-message
    confirm takeover (R4); the ketone-prompt banner during a watch with
    ketone prompts on; and the step timeline strip. No forms anywhere.
    Check: the chip never shows a button; Confirm asks for the PIN even
    right after an acknowledge used the cached one.
R2. **Morning recall cards (PWA).** Driven by recall_due: one card per
    nocturnal low with a small curve around the nadir and the question
    "At 2:47 AM you were 58 for about 25 minutes. Do you remember that?",
    four one-tap answers (I felt it and treated it / I woke up but felt
    nothing / I don't remember it / I was awake anyway →
    felt_and_treated / woke_no_symptoms / dont_remember / was_awake);
    when carbs_logged_within_30min is true the card says "you logged
    carbs at 3:05" and asks only "did you feel symptoms?" (yes / no).
    Never pre-select an answer; an unanswered card disappears at noon and
    the record says no answer. During an active watch a second strip
    (Fine / Rough / Can't eat) from symptom_check_due, also one tap, and
    a weekly "I took my shot" entry (dose label echoed and confirmed like
    every insulin entry). Taps POST /api/rounds/recall/{low_event_id},
    the stomach endpoint, and the treatment endpoint with the
    sessionStorage PIN. One morning notification, best effort. Check: a
    tap shows up as the night's answer in the PWA's Rounds tab; the
    kiosk never shows a button for it.
R3. **PWA Rounds tab.** Pairing status + Revoke (and "Share with my
    doctor", which calls the PIN-gated /api/pair/start and sends you to
    the device's QR screen), "What my doctor sees" (every card the
    device sent; the inbox's card renderer is plain JS and the app's
    Rounds tab is a React component built from the same generated card
    type, so the two render the same fixture identically), the
    glucagon-on-hand toggle with its expiry date, today's check-in
    status, and the step timeline during a watch (planned steps, holds
    shown as shifted dates, the next gate date). Check: the preview
    matches the card in the inbox pixel for pixel on the same fixture.
R4. **Doctor-message confirm takeover (display + PWA).** When
    doctor_message_received arrives, a takeover echoes the message in
    plain words ("Dr. Patel: basal 22 units from tomorrow. Confirm?",
    "hold the next step 4 weeks. Confirm?", "start a Step Watch:
    tirzepatide 2.5 mg, first increase in four weeks. Confirm?") with
    Confirm and Decline (fresh-PIN endpoints: the kiosk re-prompts the
    keypad; the PWA uses its sessionStorage PIN). An insulin change is
    echoed with the units, the same rule as voice logging. Never
    auto-confirms, never dismisses on its own; disappears on
    doctor_message_resolved. Check: Decline leaves the plan and
    Settings.basal_units unchanged in the PWA.
R5. **Clinician inbox (clinician/,
    doctor.irin-out-of-sleep-at-hackgt.tech, "mock Ascend").** Pages, in
    the order the demo needs them: pairing (reads token and device_pk
    from the URL fragment; generates the doctor keypair with tweetnacl-js
    into localStorage; posts doctor_pk with a display name; shows code4;
    waits for the device's confirmation, then stores the bearer; no
    typed-code path at the hackathon); inbox list (polls GET /v0/inbox
    every 5 s with the bearer, decrypts each card, orders red, then
    amber, then one digest line for greens, with program, source (Irin
    Bedside / Irin Brain), and DEMO badges, and Spark offers pinned at
    the top); Standing card detail (a strip of the 14 nights colored by
    reason code with logged and inferred visually distinguished, the
    rise chart on clean nights only, the excluded nights with their
    reasons, the metrics table with a confidence label on every row,
    never a blank row, the narrative, and the actions the card's
    allowed_actions lists); watch card detail (step timeline, low-point
    chart against the baseline band, metrics with confidence labels, the
    tolerance strip, the awareness row, narrative, actions); the offer
    page with the Step Watch plan setup form (drug class and schedule
    pre-filled from a small JSON of label schedules and editable, start
    date, on insulin, the three options), whose Accept sends a
    plan_create DoctorMessage; action modals: adjust basal (typed units
    and a start date), hold 2 / 4 / 8 weeks, adjust insulin, proceed,
    message patient, schedule visit, ask patient, dismiss, end watch, all
    sent as sealed DoctorMessages, with "Patient confirmed 07:14" shown
    from the message resolution; the resources flow (CATEGORY first, then
    brand, then the mock Ascend handoff screen with the banner "No
    patient data shared with any manufacturer", then POST
    /v0/resources/request; hidden when the card's resource_categories is
    empty); the "what Impiricus sees" panel (GET /v0/log: IDs, sizes,
    timestamps, ciphertext prefixes; never cut from the demo); and,
    last, the optional night replay (tap a night, rendered from the
    card's own nights list like game film, about 20 s). Styled to read as
    a screen inside Ascend, with a clear "mock" label in the footer.
    Check: pair a team phone, receive the Basal Check card from the
    basal-change scenario, tap adjust basal, see "Patient confirmed"
    after the device confirm; seek to step 2 and read the amber worked
    example; the resources handoff shows the banner.
R6. **Demo panel additions:** jump to step N day D (the replay seek),
    send card (Bedside | Brain-only), brain_only toggle, simulate Spark
    offer, start watch. Greyed in live mode. Check: each control changes
    something visible in the inbox, and the Brain-only card differs from
    the Bedside card only in its confidence labels, never in a blank
    row.

## Build order — Night Buddy demo tier (gate: Rounds R1-R11 landed with a full day left)

B1. **PWA buddy card and treating button.** Buddy status (paired name,
    twin | mirror, revoke, this morning's line), the four opt-in toggles
    (have buddy, be watcher, hub-watchable, hub-volunteer; all off by
    default), the emergency-script editor, and the TREATING button: one
    giant "I'm treating" on the app home while an alert is open and as
    the action on the alert notification — ONE thumb press, no
    confirmation step (invariant 17). Check: on a real phone, lock screen
    notification → one tap → treating_set arrives.
B2. **Watcher page (watch/, watch.irin-out-of-sleep-at-hackgt.tech).**
    Pairs as the browser peer with the
    same pairing code as the inbox (copy the file). Buddy alert screen:
    huge and single-purpose ("Sam's alarm has been unacknowledged for 12
    min, presence confirmed" or "no response to phone alerts,
    unconfirmed"), with Call (brokered, POST /v0/hub/call; no number ever
    shown) and, after that, Execute script (shows the patient's script
    only while the claim is live). Hub list: pseudonymous listings with
    the confidence label always visible and visually distinct, ordered by
    urgency then confidence, "I've got this" claim button, "claimed, in
    progress" for everyone else, automatic reopen when a lease expires,
    "marked treating 2 min ago" when the patient taps treating, TOP
    urgency when treating expires unrecovered. The morning line and the
    event close-out render on the buddy card. Check: the 45-second beat
    from DEMO_SCRIPT.md on a teammate's phone end to end.
B3. **Split-screen demo arrangement** with Slavik: device on the table as
    the patient, a teammate's phone as the mirror buddy, both visible to
    the camera and the judge.

## Invariants that bind this lane

Stale is always visibly stale (never render a frozen number as fresh);
the confirm step on insulin is never bypassed client-side; the acknowledge
button goes through the PIN-gated endpoint; the doctor-message takeover
never auto-confirms; fresh-PIN verbs on the kiosk always re-prompt; a
family story is never shown as sent unless the backend said so; cards,
alerts, and listings render with their DEMO, program, source, and
confidence badges exactly as the data says, every card metric shows its
confidence label, and no card row is ever blank; the hub and alert screens
never show a glucose value, a location, or a phone number; the treating
button is one press. Verification is a browser refresh and a screenshot,
plus the real-phone check per feature.
