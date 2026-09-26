# /review — adversarial pre-merge review of one branch

You are reviewing the diff between the current branch and dev (or, when
reviewing dev itself before a human promotes it to main, between dev and
main). You did not write this code; assume it is faulty until the diff
proves otherwise. Review ONLY what the diff touches plus anything it
plausibly breaks. Do not fix anything unless the human asks; report
findings. Never perform any merge yourself, and never any merge into
main — that promotion is done by a human only.

Run `git diff dev...HEAD` (or `git diff main...dev` for a promotion
review) and read every changed file in full, not just the hunks.

Check five axes, in this order:

1. **Correctness** — does the change do what its branch name, commits, and
   journal entry claim? Trace the main path and one failure path by hand.
   If the branch belongs to a sponsor tier (Rounds, Night Buddy), confirm
   its gate in CLAUDE.md "Build sequencing" was actually met; a Rounds
   branch merged before The Save plays end to end, or a Night Buddy
   branch before R1-R11 landed, is a finding.
2. **Project safety invariants** — if the diff touches alarms, logging,
   data sources, presence, or the frontends: stale data always shown
   stale, demo/replay data always badged DEMO and never rendered as live,
   insulin never stored without echo-and-confirm, low alarms override
   quiet hours, acknowledging a predicted-low warning never suppresses
   the actual-low alarm, Away gates room outputs only (hal layer; never
   alarm logic, and radar absence never sets Away in the night window),
   timers read clock.py (never time.time or sleep), mutating endpoints
   behind the PIN gate.
   If the diff touches backend/app/rounds/, relay/, frontend/clinician/,
   cards, plans, pairing, or doctor messages: no dose recommendation in
   any card, template, prompt, or allowed_actions; every card number
   computed by code (from ml/models/nights.py, never a private copy) and
   carrying a confidence label, with no blank row in brain_only mode; the
   narrative validated against those numbers with the template fallback
   intact; no conclusion from stale nights (coverage under 85%) or from a
   window under 70% (insufficient, red still sent); Basal Check compares
   clean nights only and lists excluded nights with reasons; the noise
   budget enforced in noise.py alone (one Standing Card per type per 14
   days, one check and one gate per step, greens to the digest, red
   deduped per event with the 12 h cap, a watch suspends Basal Check and
   absorbs Hypo Response, never two programs on one day); a doctor
   message applies only after patient confirm and only from the paired
   key, an insulin change echoes its units, decline and expiry apply
   nothing, silence changes nothing, and Irin never changes a plan or
   dose itself; cards leave the device sealed to the paired key and the
   relay stores ciphertext only; is_demo carried everywhere and demo
   cards go only to demo pairings; pairing, revoke, recall, check-in, and
   confirm endpoints PIN-gated; a missing morning answer is "no answer"
   and never counts as felt or fine, nothing is pre-selected except a
   treated pre-fill from logged carbs, the unfelt-low rate divides by
   answered, and the inferred-unfelt rule never fires on a treated low;
   the AlarmEvent recorder observes only and alarm.py is untouched
   except by R14(a) vigilance, which only raises the predicted-low
   threshold for 7 days.
   If the diff touches backend/app/buddy/, the hub endpoints, or
   frontend/watch/: buddy and hub rungs additive only (no local alarm or
   the T+20 emergency-script clock delayed, quieted, or gated; a claim
   pauses nothing); volunteers only execute the patient's own script,
   which is visible only to the live claim-holder; no glucose, location,
   or contact data in any listing or alert; confidence labels always
   present and distinct; the four opt-ins separate and revocable;
   treating one press; demo events never create real listings or calls.
   If the diff touches backend/app/family_story.py, the family prompt,
   or the recipient UI: a Level 1 story never carries a glucose value
   (validator inverted per level, template fallback); a demo-mode story
   never reaches SMTP; paused or revoked recipients receive nothing; a
   no-data night is never "fine"; the first story waits for approval.
   If the diff touches auth.py, display.js, or frontend/app/src (App.tsx
   or the usePin hook): fresh-PIN endpoints (FRESH_PIN_ENDPOINTS) never
   accept a stored kiosk PIN, the app never moves its PIN out of
   sessionStorage, and the owner pairing token is never placed in a URL
   query.
   If the diff touches cloud/, backend/app/forward.py, relay/store.py,
   relay/directory.py, relay/notify.py, or frontend/app/ (My Irin, Find
   a Buddy): no glucose value in any relay collection, listing, profile,
   match, or WhatsApp payload (grep relay/notify.py and the buddy prompts
   for mgdl); the matching score stays the deterministic Atlas
   aggregation over the rows the user confirmed (the Muse-written
   introduction, why-this-match line, emergency script, and parsed
   availability are text beside the match or input the user confirms,
   never a scorer input); the forwarder is outbound, batched on clock.py, and
   never blocks the poller or an alarm (an unreachable cloud changes
   nothing about alarms or staleness); ingest upserts on (device_id,
   timestamp); no card, rule, or alarm reads from a Tiger aggregate;
   the agreement test still passes; the family bearer grants one rollup
   and revokes; the emergency number is encrypted before storage and
   never returned by any endpoint; 22:00 to 08:00 stays locked in the
   availability grid.
   If the diff touches narrative.py, voice_out.py, or cloud/audio.py:
   every link's output passes the validator (the chain is Backboard →
   Meta direct → direct Anthropic → template, the validator after each
   link; provider meta is chosen per task by NARRATIVE_ROUTING, never by
   NARRATIVE_BACKEND, and the eight task keys are final); a card
   narrative also passes the second opinion and disagreement ships the
   template; the family scope's memory is Readonly; a missing or failing
   key never raises in the alarm or morning-report path; clips are cached by text
   hash; a spoken clip plays after a tone, never instead of one, and the
   alarm tones stay distinct by tier.
   If the diff touches frontend/app/ (the Vite + React app, decision 28):
   `npx tsc --noEmit` is clean and `npm run build` succeeds; dist/ in the
   diff was produced by that build (its index.html references the new
   asset hashes) and is not stale; every request shape comes from the
   generated types in src/types/ (a hand-declared interface for a
   backend or relay message is a finding, as is an edited generated
   file); package.json gained no dependency the journal does not name;
   no CDN script tag anywhere in index.html or the source; no secret in
   .env.production (public URLs only); the PIN stays in sessionStorage.
3. **Security** — new endpoints gated (PIN on the Pi, source key or
   bearer on the relay), no secrets or private keys introduced, no real
   CGM data or timestamps, user input (voice text, settings values,
   check-in answers, typed insulin units, plan setup fields, hub
   payloads) validated, the
   relay never logs or stores a plaintext card field, pairing tokens
   single-use and expiring, CORS limited to the known origins.
4. **Bugs** — off-by-one on thresholds (70, 54, ±30, 70% same direction,
   5 clean nights, 3 clean nights per side for Follow-up, −15, 4.0%, 92
   readings, 70% window coverage, 3 of 5, the 20-minute run and 1.0
   mg/dL/min slope of the inferred-unfelt rule, the two-card recall cap,
   the 30-minute pre-fill window, the 3-hour and 60-minute reason-code
   windows), unhandled None from the data source, an unanswered morning
   question, or a metric that cannot be computed (None, never zero), the
   unfelt-low rate's denominator (answered, never all events), race
   between WebSocket push and store write, unawaited coroutines, lease
   and treating expiry on clock.py, duplicate cards after a seek or
   re-evaluation (card_id must derive from program, kind, period, plan,
   step).
5. **Regressions** — what existing behavior could this break? Name the test
   that would catch it; if none exists, say so — that is a finding.

Then run pytest from backend/ if the diff touches backend code, from
relay/ if it touches relay code, and from cloud/ if it touches cloud code
(the local compose containers stand in for Atlas and Tiger when Chris
runs them; everyone else points ATLAS_URI and TIGER_URI at the real
services). If it touches cloud/sql/ or ml/models/nights.py, also run
ml/tests/test_agreement.py: it runs against TIGER_URI (a scratch schema
on the Tiger Cloud service) or against Chris's compose when he runs it;
nobody but Chris needs Docker.

Output: findings ranked by severity, each with file and line, one line of
why it matters, and PASS/FAIL per axis at the end. If everything passes,
say so in one line — do not invent findings to look thorough.
