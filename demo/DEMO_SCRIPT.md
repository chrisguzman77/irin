# Irin — run of show

## Main slot (3 minutes)

TBD

## Irin Rounds — the eight beats

Roughly two minutes for the Impiricus beat, one narrative arc: detect, decide, verify,
then the pharma moment. The demo panel drives every jump (`POST /api/demo/seek`,
PIN, demo only); the inbox at doctor.irin-out-of-sleep-at-hackgt.tech is the judge's screen.
Every card is badged DEMO. If time is short, beats 2 to 4 alone tell a complete story, and
so do beats 5 to 7: rehearse both halves standalone.

### 1. Pairing
The kiosk shows "Share with my doctor": a QR code. A judge scans it with a phone, the
inbox opens at /pair, both screens show the same 4 digits, Chris confirms on the keypad
(a fresh PIN, never the cached one). The judge is now the doctor. Line: "The key never
left the phone; the device only ever learned its public half."

### 2. Detect
Say it this way, never unqualified: glucose real; reason codes inferred and labeled; acknowledge, presence, and recall data are a labeled overlay.

Scenario `basal_change_1`, seek to the morning of 2021-01-25 (numbers computed by
ml/models/nights.py from the scenario, not chosen): Basal Check fires on 8 clean nights
of 14, median overnight rise +89 mg/dL, 75% of clean nights rising; 6 excluded nights
listed with their inferred reasons. It fires every morning through 2021-01-29.

Panel: select `basal_change_1`, then `seek {"date": "2021-01-25"}`. The catch-up
replays every morning; the budget sends ONE Basal Check per 14 days, so the card in the
inbox is the first morning the rise crossed the rule (the panel's evaluations show the
01-25 numbers above). Line: "Same CGM line, opposite prescriptions. Rounds knows which."

### 3. Decide
The judge taps "adjust basal", types the units and a start date (the doctor types every
number; no card ever suggests one). The device asks Chris to confirm: the kiosk echoes
"Dr. <name>: basal N units from <date>", keypad, fresh PIN. The inbox shows "Patient
confirmed 07:14". Silence would have meant the plan stands.

### 4. Verify
Same scenario, after the confirmed change on 2021-02-01 (18 units; say "units
illustrative" unless Chris confirms they are his): the Follow-up at days 1-7 still shows
rising nights (+78.5 on 6 clean nights); at days 1-14 the overnight rise is flat (+2 on 11
clean nights). Say it as "since the change", never "the change caused".

Panel: `seek {"date": "2021-02-15"}`. Line: "Rounds would have flagged this N weeks earlier."

### 5. The therapy start
Panel: select `titration_synthetic` (say SYNTHETIC, it is labeled everywhere), then
"simulate Spark offer". The judge's inbox and the kiosk show the offer as a doctor
message: "Impiricus Spark (simulated): start a Step Watch, tirzepatide 2.5 mg, first
increase on 2020-02-12". Nothing is applied until Chris confirms it with a fresh PIN;
that confirm IS "start watch". Do the offer and the confirm BEFORE the seek: a seek
jumps days and a pending offer expires after 24 hours of scenario time.

### 6. The amber card
Panel: `seek {"step": 2, "day": 8}` (the step-2 day-7 check closes at the day-8
morning). The inbox shows the step check, amber: "Step 2 (5 mg), days 3 to 7. Overnight
low point down 22 mg/dL from baseline, 2 near-misses, time below range 4.0%. 1 low not
remembered. Rough stomach 3 of 5 days." Every row carries its label (measured / reported
/ inferred). The judge taps "hold 4 weeks" and "adjust insulin"; the kiosk echoes each,
Chris confirms each with a fresh PIN. The held step moves; the gate before the new date
is a new gate.

### 7. The pharma moment
On the amber card the stomach row offers a category ("GI side-effect education"); on a
Hypo Response card with no glucagon on file the row offers "glucagon access". The judge
taps it, picks the category, then a brand, and the mock Ascend handoff appears under
the banner "No patient data shared with any manufacturer": the request carries the
doctor id, the category and the brand, and nothing else. Flip to "what Impiricus sees":
ciphertext prefixes, sizes, kinds, timestamps. Resources are by category first, pulled
by the doctor, never pushed, labeled as manufacturer resources, off for off-label use.

### 8. Close
"Every hold is a patient who didn't quit, and every one of these cards started from the
patient, not from pharma. No glucose value ever left the doctor's hands."

### Demo data and the seek
Standing Cards run on Chris's real history: real basal increases exported as date-shifted
windows, 14 nights either side, reason codes on history INFERRED and labeled so. Step
Watch runs on the SYNTHETIC titration scenario and its companion JSON (plan, symptom
checks, injection logs, recall answers, alarm events). A 24-week watch cannot be played
(4,032 hours; 67 hours at 60x): the seek jumps the clock and the idempotent catch-up
builds every night, question and card that should exist by then; seeking twice sends
nothing twice, and a reboot mid-window gives the same cards.

## Night Buddy — the 45-second beat

TBD

## Family Story — the video beat

TBD

## Venue fallback

The one statement of it is docs/plans/chris.md R6: the same compose file on Chris's laptop serving everything on one origin at http://<laptop-ip>; nothing works at that address unchanged.

## Q&A sheet

1. Say it this way, never unqualified: glucose real; reason codes inferred and labeled; acknowledge, presence, and recall data are a labeled overlay.
2. Emergency script. Demo tier: the script is stored on the relay encrypted at rest and released only to the live claim-holder. Production: at claim time the relay notifies the device, which is by definition awake during an episode, and the device encrypts the script to the claim-holder's public key on demand, so it is end-to-end even to a volunteer unknown in advance.

### Rounds Q&A ammunition (from the Rounds spec)
- "Doesn't this flood doctors?" The noise budget: one card per type per 14 days, one per step, green in a digest, red deduplicated per event; review time may be billable. It replaces unstructured portal messages rather than adding to them.
- "How is this different from Clarity or Tidepool?" They show what the numbers did. Rounds explains why, removes the confounded nights, surfaces near-misses nothing else can see, measures alarm response and awareness, and delivers a decision instead of a dashboard.
- "GLP-1s don't cause many lows." Correct, and the consensus says why: clinicians reduce insulin proactively. This is how that reassessment happens between visits, and it watches the opposite risk too.
- "Isn't self-report weak evidence?" It is the current clinical standard, and the two standard questionnaires disagree about the same patient nearly 60% of the time. We ask about one specific low, the next morning, with the curve attached, and the device confirms it objectively where it matters.
- "Who buys the device?" Nobody has to. Rounds runs on any CGM feed. Irin Bedside is the confirmatory step doctors recommend, and during a titration it can be a clinic loaner.
- "Can Impiricus see patient data?" No, and the panel shows it. They would still sign a BAA, because encryption is about trust, not exemption.
- "What if someone spoofs a dose change?" Replies are authenticated with the paired doctor's key, the relay cannot forge, and the patient must still echo-confirm.
- "What if the AI invents a number?" Code computes every number and a validator rejects any that is not in the metrics.
- "Is this a medical device?" No card contains a dose recommendation. Production would go through FDA clinical decision support review and BAAs.
- "Isn't one patient too little evidence?" Yes. n of 1, observational, retrospective, presented as proof of concept.
- "Predicting lows isn't new." Agreed. The value is room-scale waking, context, two tiers, and Rounds.
- "Why would device makers pay when they have portals?" Portals are pull. Rounds pushes decisions into a channel doctors already use, including the digitally dark ones portals never reach.

Numbers to memorize: 8.7% glucagon fills; 17.5% impaired awareness with about 4x severe lows; questionnaires disagree ~60%; 25 to 35% insulin reduction on GLP-1s; reassess within 2 to 3 days and at every 4-week step; over half stop within a year; $396 per patient per year from 95251; about 6.3M type 2 insulin users, roughly 3x the type 1 population; 13 touchpoints per tirzepatide watch.

### Compliance language and honest limits
Resources by category first, the doctor chooses the brand, always pulled, never pushed, labeled as manufacturer resources, off for off-label use. Never say "sell more insulin" or "sell more GLP-1s": say under-titration, access, glucagon underprescribing, persistence, tolerance, safe titration. Persistence lift is a hypothesis Impiricus would measure, not a result. Historical reason codes are inferred. Everything is one patient, observational: "since the change", never "the change caused". Glucagon status is patient-reported; Irin cannot see prescriptions; tolerance, injection and recall data are patient-reported too. Rounds never prescribes; e-prescribing stays in the EHR. No pharma funding of patient devices (anti-kickback); clinic-owned loaners or device-maker programs are the safe versions. Irin remains a companion to CGM alarms, not a replacement and not a medical device.

(The Night Buddy Q&A table from its spec is pasted in by Chris when that tier is decided.)
