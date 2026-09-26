# Irin — run of show

## Main slot (3 minutes)

TBD

## Irin Rounds — the eight beats

### 1. Pairing
TBD

### 2. Detect
Say it this way, never unqualified: glucose real; reason codes inferred and labeled; acknowledge, presence, and recall data are a labeled overlay.

Scenario `basal_change_1`, seek to the morning of 2021-01-25 (numbers computed by
ml/models/nights.py from the scenario, not chosen): Basal Check fires on 8 clean nights
of 14, median overnight rise +89 mg/dL, 75% of clean nights rising; 6 excluded nights
listed with their inferred reasons. It fires every morning through 2021-01-29.

### 3. Decide
TBD

### 4. Verify
Same scenario, after the confirmed change on 2021-02-01 (18 units; say "units
illustrative" unless Chris confirms they are his): the Follow-up at days 1-7 still shows
rising nights (+78.5 on 6 clean nights); at days 1-14 the overnight rise is flat (+2 on 11
clean nights). Say it as "since the change", never "the change caused".

### 5. The therapy start
TBD

### 6. The amber card
TBD

### 7. The pharma moment
TBD

### 8. Close
TBD

## Night Buddy — the 45-second beat

TBD

## Family Story — the video beat

TBD

## Venue fallback

The one statement of it is docs/plans/chris.md R6: the same compose file on Chris's laptop serving everything on one origin at http://<laptop-ip>; nothing works at that address unchanged.

## Q&A sheet

1. Say it this way, never unqualified: glucose real; reason codes inferred and labeled; acknowledge, presence, and recall data are a labeled overlay.
2. Emergency script. Demo tier: the script is stored on the relay encrypted at rest and released only to the live claim-holder. Production: at claim time the relay notifies the device, which is by definition awake during an episode, and the device encrypts the script to the claim-holder's public key on demand, so it is end-to-end even to a volunteer unknown in advance.

(The Rounds Q&A ammunition and the Night Buddy Q&A table from the challenge specs are pasted in by Chris at R13.)
