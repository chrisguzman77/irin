# Irin relay — the blind courier (\"mock Impiricus\")

The relay stores ciphertext and profile fields only. It can never read a
card, a doctor message, or a buddy alert: every payload is sealed on the
device to the recipient's public key (PyNaCl crypto_box) and opened only in
the recipient's browser. Nothing in any relay collection is ever a glucose
value (CLAUDE.md invariants 11, 15, 20). Readings and dashboards live in the
separate Irin Cloud service (`cloud/`). Storage is MongoDB Atlas
(`store.py`; the laptop compose runs a local mongo instead).

## Auth model

- **Devices** send `X-Source-Key` (one of `RELAY_SOURCE_KEYS`).
- **Inbox and watcher pages** send the bearer issued at pairing confirm
  (`Authorization: Bearer ...`).
- **Public** routes (pairing state/complete, owner-pairing redeem) need
  neither; they are single-use tokens or codes.
- `RELAY_ADMIN_KEY` gates the demo-only Spark simulation.

## Envelope (what the relay stores for a card, message, or alert)

| field | meaning |
|---|---|
| recipient_id | the paired peer the payload is sealed to |
| sender_id | the device (or the doctor) that sealed it |
| nonce | crypto_box nonce, base64 |
| ciphertext | the sealed payload, base64; the relay never opens it |
| source | irin_bedside \| irin_brain |
| kind | the card kind, or buddy_alert / buddy_line, or the doctor-message kind |
| program | standing \| step_watch (cards); absent or buddy for kind buddy_alert |
| is_demo | outside the ciphertext; must match the pairing's is_demo or the relay rejects it |

## Relay API v0 (keep identical to docs/plans/chris.md)

- `POST /v0/cards` {recipient_id, sender_id, nonce, ciphertext, source, kind, program, is_demo} (source key). `GET /v0/inbox/{recipient_id}?since=` (bearer).
- `POST /v0/messages` {device_id, message_id, nonce, ciphertext, kind, is_demo} (doctor -> device, sealed to the device key, bearer; the sender is the bearer's pairing; message ids are single use). `GET /v0/device/{device_id}/messages` (source key; the Pi polls) -> `{messages: [envelope + sender_id, status, created_at], pairings: [{doctor_id, status, peer_kind}], calls: [{listing_id, claim_id, at}]}` (calls: B3 hub calls, each delivered once) so a revoke from the inbox reaches the device. `POST /v0/messages/{id}/resolution` {status: confirmed | declined | expired} (source key; metadata only). `GET /v0/messages/{id}` (bearer, the sender's) -> the resolution word and time ("Patient confirmed 07:14").
- Cards: `POST /v0/cards` upserts by `card_id` when given (a re-evaluation never duplicates a card); the recipient must hold a confirmed pairing whose is_demo matches the envelope's (409 otherwise). `GET /v0/inbox/{recipient_id}` needs that recipient's own bearer (403 for another's). `GET /v0/log?limit=` is public metadata: route, ids, kinds, sizes, ciphertext prefixes, timestamps.
- Rate limit: 600 requests per minute per source key, per bearer, and one shared bucket for the public routes (429), RELAY_RATE_LIMIT_PER_MIN.
- Pairings registered before the source-key ownership rule (no `source_key_hash`) must be re-paired.
- One source key = one device: a key may only confirm, revoke, post cards to, and poll the pairings it registered (`device_id` in `POST /v0/pair`). A doctor message is routed to the bearer's paired device; a `device_id` in the envelope that names another device is 403. The plaintext bearer waits at most 10 minutes for the browser after confirm and is handed over exactly once (atomic), so anyone who photographed the QR could at most race the doctor for a bearer that yields ciphertext and metadata, never plaintext. RELAY_EXTRA_ORIGINS adds the laptop-fallback origins to CORS.
- `POST /v0/pair` {token, device_pk, is_demo} (source key). `GET /v0/pair/{token}` (public). `POST /v0/pair/{token}/complete` {doctor_pk, doctor_display_name} (public, single use). `POST /v0/pair/{token}/confirm` (source key; issues the bearer). `POST /v0/pair/{id}/revoke` (source key or bearer).
  Pairing shapes (R5, the Pi's side is backend/app/rounds/pairing.py): `POST /v0/pair` body also carries `peer_kind` (doctor | buddy). `GET /v0/pair/{token}` -> `{status: pending | completed | confirmed, doctor_pk?, doctor_display_name?, is_demo, peer_kind}` (404 once expired or unknown). `POST /v0/pair/{token}/complete` -> `{status: "completed"}` (409 if already completed). `POST /v0/pair/{token}/confirm` -> `{pairing_id, doctor_id, bearer_issued: true}` (the bearer itself goes to the browser through `GET /v0/pair/{token}` after confirm, once, as `bearer`). `POST /v0/pair/{id}/revoke` -> `{status: "revoked"}` and the relay forgets the doctor_pk and the bearer. Both sides show `code4` = first 4 bytes of sha256(UTF-8 of device_pk_b64 + doctor_pk_b64 + token_hex) as a big-endian integer, mod 10000, zero-padded; keys are base64 (standard, padded), tokens 32 hex chars, nonces base64. The QR URL is `<inbox or watch page>/pair#token=...&device_pk=...&relay=...`: the fragment never reaches a server. The Pi's own `GET /v0/pair/{token}` and confirm calls do put the token in a path (single use, 10 minutes, confirm needs the source key); Caddy's access log for api.<domain> should not log `/v0/pair/*` paths. Revoke takes the doctor_id (the relay also accepts the token).
- Owner pairing (A2, R5+): the phone app <-> this user's Pi. PINNED 2026-09-27 for three parallel sessions (relay, Pi, app + kiosk); build exactly this.
  - ONE owner pairing per device: a new registration replaces the device's earlier unused code; a new redeem revokes the device's earlier paired token.
  - `POST /v0/device/pairings` (X-Source-Key) {code, device_id, device_url, token, expires_at} -> {status: "registered"}. code = 6 digits (string), token = 43+ url-safe chars minted by the Pi, expires_at ISO (10 wall minutes). device_id must be the source key's device. The relay keeps the plaintext token ONLY until the code is redeemed, then only sha256(token).
  - `POST /v0/device/pair` (public, rate-limited per IP) {code, username} -> {device_id, device_url, token}. username = 1-40 chars, a label for the phone ("Chris's phone"). Single use. Unknown, expired, or used code -> 404 with one message for all three ("that code is not valid; show a new one on your Irin"). The relay then drops the plaintext token.
  - `DELETE /v0/device/pair` with `Authorization: Bearer <owner token>` (the app) OR X-Source-Key (the Pi, revokes that device's owner pairing) -> {status: "revoked"}; 401 on an unknown token. Idempotent.
  - Device poll gains `owner: {state: "none" | "pending" | "paired" | "revoked", username?, paired_at?, token_sha256?}` (the device's current owner pairing; token_sha256 lets the Pi check it is the token it minted). Existing keys unchanged.
  - Audit: ids and states only; never the code, token, or username in the log.
  - Pi side (backend/app/owner.py): `POST /api/owner/code` (require_pin; the kiosk keypad) -> {code, qr_url, expires_at, expires_in_s}; 409 "stand in front of your Irin to pair a phone" unless hal.get_presence() reads True (mock: allowed); qr_url = APP_ORIGIN + "/#pair=" + code (a fragment: never sent to a server; it is the code, never the token). `GET /api/owner` (no PIN) -> {state, username, paired_at}, never the token. `DELETE /api/owner` (require_pin) -> revokes locally and on the relay. The Pi learns redeem/revoke from the poll and stores kv owner_pairing {token_sha256, state, username, paired_at}; broadcasts WS `pairing_state` {kind: "owner", state, username}. Enforcement: a PIN-gated request that arrived through the tunnel (header Cf-Connecting-Ip present) must ALSO carry `Authorization: Bearer <owner token>` matching the paired token_sha256, else 401 "pair this phone with your Irin"; requests without Cf-Connecting-Ip (the kiosk on localhost, the LAN, tests) are unchanged. Reads and /ws are unchanged (known: snapshot stays readable).
  - App side: the Device tab's unpaired screen redeems the code (typed, or prefilled from `#pair=` then the fragment is cleared) with a username field; stores {device_id, device_url, token} in localStorage (`irin.owner`); sends `Authorization: Bearer <token>` plus X-PIN on every Device-tab call to device_url; a 401 from the Pi or relay clears it back to unpaired. "Disconnect this phone" (Device tab settings) = relay DELETE /v0/device/pair with the bearer, then clear. VITE_DEVICE_URL dev override unchanged.
  - Kiosk side: a "Pair a phone" control (PIN keypad) -> POST /api/owner/code -> the QR (qrcode.js) of qr_url + the 6 digits large + a countdown; 409 shows its detail; closes on WS pairing_state {kind: "owner", state: "paired"} or expiry. "Disconnect phone" (PIN) -> DELETE /api/owner; shows the paired username from GET /api/owner.
- `POST /v0/spark/new_rx` (demo-only, admin key; no patient identity).
- `POST /v0/resources/request` {doctor_id, category, brand} (bearer; doctor_id must be the bearer's own, 403 otherwise) -> {request_id, status: handed_off, category, brand, banner, shared_fields: [category, brand]}: the only thing a pharma-side system would ever see; no patient field, no card id, no number. Categories (the same tokens a card's `resource_categories` carries): glucagon_access, gi_side_effect_education, copay_savings, samples_next_pen, bridge_supply, prior_auth_hub, ask_msl; a card with an empty list (off-label use) offers no handoff. `GET /v0/resources` (bearer) lists the doctor's own handoffs, newest first. The banner the inbox shows on the handoff screen is the response's `banner`: "No patient data shared with any manufacturer".
- `GET /v0/log`: the \"what Impiricus sees\" view (routes, device and peer ids, kinds, timestamps, sizes, ciphertext prefixes; never a plaintext field, and no card or message ids).
- Hub (B3): `POST /v0/hub/listing`, `GET /v0/hub/list`, `POST /v0/hub/claim`, `GET /v0/hub/claim/{id}/script`, `POST /v0/hub/call`, `POST /v0/hub/treating`, `POST /v0/hub/resolve`, `GET /v0/hub/audit`. Shapes in "Hub (B3)" below.
- Buddy alerts (B3) need no route of their own: the device posts a sealed envelope to `POST /v0/cards` with `kind: "buddy_alert"`, `program` absent or `"buddy"`, and `recipient_id` = the buddy pairing's doctor_id (a pairing with `peer_kind: buddy`); the watcher page reads it from `GET /v0/inbox/{peer_id}` with its bearer. A buddy pairing accepts buddy_alert and buddy_line and nothing else, and a doctor pairing never accepts either (409); `program: buddy` on any other kind, or `standing`/`step_watch` on a buddy kind, is 422.
- `kind: "buddy_line"` (B5; program `buddy` or absent) is the second envelope kind a buddy pairing accepts: a sealed `{line, kind: "buddy_line", night_date}` (the morning "all quiet" line or the episode close-out; a first name, counts and clock times, never a glucose value), `card_id: "bl-<night_date>"` so a re-send replaces it.
- Timestamps: every timestamp the relay returns is ISO 8601 with an explicit offset (`+00:00`; the Mongo client is tz_aware).
- Buddy directory (B3+), PINNED 2026-09-26 so three sessions build in parallel (relay, Pi, screens). Profiles and matches never hold a glucose value, a location, a phone number or an email (422). The scorer is deterministic (relay/directory.py header); a model never picks a buddy.
  - `POST /v0/users` (X-Source-Key; one user per source key, upsert) body `{username, first_name, languages: [str], timezones: [IANA], availability: [{weekday 0-6, start "HH:MM", end "HH:MM"}] (local), optins: {have_buddy, be_watcher, hub_watchable, hub_volunteer}, cgm_verified: bool, is_demo}` -> `{user_id, user_bearer}` (the bearer is returned on every call for this source key's user; the Pi keeps it). The Pi verifies the CGM feed itself and sends only `cgm_verified`; the feed URL and token never reach the relay. Unverified users are never listed or matched.
  - `GET /v0/users/search?username=` (Authorization: Bearer <user_bearer>) -> `[{user_id, username, first_name, languages, be_watcher}]`, CGM-verified only, demo users only to demo users.
  - `POST /v0/match` (user bearer) `{}` -> up to 3 `{match_id, candidate_id, first_name, score, hours_covered, mirror, shared_languages, status: "offered"}`, sorted by score; candidates are CGM-verified, `be_watcher` on, same is_demo; declined candidates never return; the same pair re-offered keeps its match_id.
  - `POST /v0/match/{match_id}/accept | /decline` (user bearer; either side) -> the match with `status` (`offered` until BOTH accepted, then `accepted`; `declined` as soon as either declines). Revoking later is the existing pairing revoke.
  - `POST /v0/match/{match_id}/pair_link` (X-Source-Key of an accepted side) `{pair_url}` -> `{stored: true}`: the device's buddy pairing link (`qr_url` from the Pi's `POST /api/pair/start {peer_kind: "buddy"}`), handed to the other side.
  - The device poll `GET /v0/device/{device_id}/messages` gains `matches: [{match_id, first_name, status, pair_url | null}]` for this device's user (keep every existing key unchanged).
  - Every directory change is written to `/v0/log` with ids and kinds only.
  - Decided at build time (b4cc81c): candidates = CGM-verified, `be_watcher` on, same is_demo (no timezone or language pre-filter, so mirrors and a zero-overlap candidate still score); hours_covered = average covered hours per night over the week (0-10, one decimal), a user's FIRST timezone is home, offsets read now; an availability row whose end is not after its start runs past midnight; mirror uses the wrap-around offset difference; `user_bearer` is stable per source key (hash stored); usernames 3-30 chars starting with a letter, search = case-insensitive prefix, max 20, lists users with have_buddy or be_watcher on; `POST /v0/match` 403 when the requester is unverified or have_buddy is off, search 403 when unverified; accepting a declined match or declining an accepted one is 409; an accepted pair is never re-offered; `pair_link` before both accept is 409, from a device outside the match 404; in the poll, `pair_url` is the OTHER side's link.

Pairing, cards, inbox, messages, the device poll, resolutions, and the log are live (R6); resources are live (R13); the hub is live (B3); the WhatsApp channel is live (B4+); the buddy directory is live (B3+, relay/directory.py); owner pairing is live (A2, pinned above; redeem capped 10/min per IP and 30/min overall); the Spark route is a 501 stub (the Pi simulates the Spark sender).

## Hub (B3)

Listings carry no glucose value, location, or contact (invariant 15), every
claim is audited (16), and demo listings live in a separate pool (18).
Volunteer = the bearer of a confirmed pairing with `peer_kind: buddy` (any
other bearer is 403). Device = `X-Source-Key`; a key touches only the
listings it created (404 otherwise). Expiry is lazy: every hub route first
reopens listings whose lease has expired and returns expired treating
listings to open at TOP urgency, on the relay clock (`store.now`).

- `POST /v0/hub/listing` (source key) body `{listing_id, first_name, confidence: device_confirmed | unconfirmed, elapsed_min, urgency, is_demo, event_id, script?, script_ciphertext?, script_nonce?}` -> HubListing. Any other field is 422, and so is any field whose name contains glucose, mgdl, location, lat, lon, phone, email, or number. first_name is 1-40 characters with no digit and no `@`. Upserts by listing_id (the device re-posts with a new elapsed_min; the relay owns `status`); `is_demo` cannot change on a listing (409). A listing that came back at TOP urgency keeps it when re-posted with a lower urgency.
- HubListing: `{listing_id, first_name, status: open | claimed | treating | resolved, confidence, elapsed_min, urgency, claim_expires_at, treating_expires_at, is_demo}` (the two expiries ISO or null).
- `GET /v0/hub/list` (volunteer bearer) -> HubListing[]: every listing not resolved, in the bearer's pool only (demo bearer sees demo listings, real sees real), ordered by urgency desc, then device_confirmed before unconfirmed, then oldest first.
- `POST /v0/hub/claim` `{listing_id}` (volunteer bearer) -> HubClaim `{claim_id, listing_id, volunteer_id, claimed_at, expires_at, actions, outcome}` with `actions: ["claim"]`, `outcome: null`. Exclusive lease of `HUB_LEASE_S` (default 180). Only an `open` listing can be claimed: otherwise 409 `{detail, holder_expires_at}` (the live holder's expiry, null when the listing is treating or resolved). A listing in the other pool is 404. When the lease expires the claim closes with outcome `lease_expired` and the listing is `open` again.
- `GET /v0/hub/claim/{claim_id}/script` (volunteer bearer) -> `{steps: [...]}`. 404 unknown claim (or no script on the listing), 403 not the bearer's claim, 410 once the lease is over (expired, or closed by resolve). Each read appends `script` to the claim's actions.
- `POST /v0/hub/call` `{claim_id}` (volunteer bearer, live claim only; same 404/403/410) -> `{status: "ringing"}`. Queues a call for the listing's device, delivered once through the existing device poll `GET /v0/device/{device_id}/messages` as the new key `calls: [{listing_id, claim_id, at}]` (matched by the source key that created the listing; one source key = one device). No telephony, no number. Appends `call` to the claim's actions.
- `POST /v0/hub/treating` `{listing_id}` (source key) -> HubListing with `status: treating` and `treating_expires_at` = now + `HUB_TREATING_S` (default 1200). A treating listing cannot be claimed (409). If no resolve arrives by then, it returns to `open` with urgency = max(urgency of every open listing, its own) + 1 (TOP). 409 on a resolved listing.
- `POST /v0/hub/resolve` `{listing_id, outcome}` (source key; outcome `^[a-z_]{1,40}$`, e.g. recovered, acknowledged) -> HubListing with `status: resolved`; a live claim closes with that outcome.
- `GET /v0/hub/audit` (source key) -> every claim on this device's listings, oldest first: `{claim_id, listing_id, volunteer_id, claimed_at, expires_at, actions: [claim, script, call, ...], outcome}`. Each hub action (hub.listing, hub.claim, hub.script, hub.call, hub.treating, hub.resolve, hub.lease_expired, hub.treating_expired) is also written to the `/v0/log` audit with ids and kinds only, never the first name.
- The script (demo tier), what the Pi sends: `script: {"steps": [...]}` in the clear over this key-gated HTTPS route (at most 20 steps of 500 characters); the relay seals it at rest with `HUB_SCRIPT_KEY` and opens it only for the live claim-holder. Alternatively the device seals `{"steps": [...]}` with NaCl secretbox under `HUB_SCRIPT_KEY` (32 bytes, base64, shared by the device and the relay) and posts `script_ciphertext` + `script_nonce` (both or neither; a script that does not open or is not `{steps: [str]}` is 422). The relay stores only the sealed form and opens it only for the live claim-holder. **If `HUB_SCRIPT_KEY` is unset the relay generates a random key per process** (and says so at boot): device-sealed scripts then never open, and a restart orphans stored scripts, so set it in production. The production answer (the device seals the script to the claim-holder's public key on demand at claim time) is in docs/plans/chris.md B3.
- Env: `HUB_LEASE_S` (180), `HUB_TREATING_S` (1200), `HUB_SCRIPT_KEY`. Earlier deployments' TTL indexes on the hub's expiry fields are dropped at boot (a janitor deleting the listing would lose the TOP-urgency return).

## WhatsApp channel (B4+, relay/notify.py)

The second channel beside the watcher page's voice loop. The buddy's number is a contact detail: stored on the pairing document only, never returned by any GET, never written to `/v0/log`, and deleted with the pairing's keys on revoke.

- `POST /v0/buddy/whatsapp` `{phone}` (bearer of a confirmed pairing with `peer_kind: buddy`; a doctor bearer is 403) -> `{registered: true}`. `phone` is E.164 (`^\+[1-9]\d{7,14}$`, e.g. `+14045550123`, no spaces), else 422. Posting again replaces the number. The watcher page adds the number field; the buddy must also send "hi" to the Irin WhatsApp number (free-form messages only reach them inside WhatsApp's 24-hour window).
- `POST /v0/buddy/notify` `{peer_id, first_name, minutes, audio_url, is_demo}` (source key; the Pi calls it best effort after sealing a buddy_alert to that buddy) -> `{sent: bool, reason: ok | audio_failed | no_number | not_configured | graph_error}`. `peer_id` = the buddy pairing's doctor_id: 404 unknown, 403 registered by another source key, 409 not `peer_kind: buddy` or `is_demo` not the pairing's. `first_name` 1-40 characters, no digit and no `@`; `minutes` 0-1440; `audio_url` null or an Irin Cloud clip URL (`.../v1/audio/<64 hex>.mp3`). Any other field is 422, and so is any field whose name contains glucose, mgdl, mg_dl, location, or phone. When a number is registered and `WHATSAPP_TOKEN` + `WHATSAPP_PHONE_NUMBER_ID` are set, two Graph calls go to `POST https://graph.facebook.com/v21.0/{WHATSAPP_PHONE_NUMBER_ID}/messages` (Bearer WHATSAPP_TOKEN): a text "Irin: your buddy <first_name> is in trouble. The alarm has been unacknowledged for <minutes> minutes. Open https://watch.<DOMAIN>" (prefixed `[DEMO] ` for a demo pairing), then, with an audio_url, `{type: audio, audio: {link: audio_url}}`. `sent` is true once the text went out (`audio_failed` = the clip did not). `/v0/log` gets `buddy.whatsapp` and `buddy.notify` rows with ids and outcomes only.
- Env: `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID` (server only), `DOMAIN` (the watch link).

## Clinical Signal Card v0 (the sealed plaintext)

One schema, two programs: the `program` field decides which sections a
renderer shows. Any device maker could publish cards into this channel. A
card never contains a dose recommendation; every metric carries a confidence
label (measured / reported / inferred); a card never shows a blank row.
Generated from `backend/app/contracts.py::SignalCard`; a schema change is
announced like a contracts change.

```json
{
  "description": "Clinical Signal Card v0 (published in relay/README.md). One schema,\ntwo programs; `program` decides which sections a renderer shows. Every\nmetric carries a confidence label; a card never shows a blank row; a\ncard never contains a dose recommendation.",
  "properties": {
    "card_id": {
      "title": "Card Id",
      "type": "string"
    },
    "schema_version": {
      "default": "0",
      "title": "Schema Version",
      "type": "string"
    },
    "program": {
      "enum": [
        "standing",
        "step_watch"
      ],
      "title": "Program",
      "type": "string"
    },
    "kind": {
      "enum": [
        "basal_check",
        "hypo_response",
        "follow_up",
        "early_check",
        "step_check",
        "step_gate",
        "safety",
        "graduation",
        "baseline_note"
      ],
      "title": "Kind",
      "type": "string"
    },
    "status": {
      "enum": [
        "green",
        "amber",
        "red",
        "insufficient"
      ],
      "title": "Status",
      "type": "string"
    },
    "flags": {
      "items": {
        "enum": [
          "lows",
          "highs",
          "awareness",
          "tolerance",
          "level2",
          "rearm",
          "ketone_risk",
          "baseline_thin",
          "dose_mismatch",
          "missed_injection",
          "rise_high",
          "rise_low"
        ],
        "type": "string"
      },
      "title": "Flags",
      "type": "array"
    },
    "confidence": {
      "additionalProperties": {
        "enum": [
          "measured",
          "reported",
          "inferred"
        ],
        "type": "string"
      },
      "title": "Confidence",
      "type": "object"
    },
    "source": {
      "enum": [
        "irin_bedside",
        "irin_brain"
      ],
      "title": "Source",
      "type": "string"
    },
    "patient_pseudonym": {
      "title": "Patient Pseudonym",
      "type": "string"
    },
    "plan_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Plan Id"
    },
    "step_index": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Step Index"
    },
    "period_start": {
      "format": "date",
      "title": "Period Start",
      "type": "string"
    },
    "period_end": {
      "format": "date",
      "title": "Period End",
      "type": "string"
    },
    "headline": {
      "title": "Headline",
      "type": "string"
    },
    "metrics": {
      "additionalProperties": true,
      "title": "Metrics",
      "type": "object"
    },
    "nights": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "title": "Nights",
      "type": "array"
    },
    "excluded_counts": {
      "additionalProperties": {
        "type": "integer"
      },
      "title": "Excluded Counts",
      "type": "object"
    },
    "tolerance_days": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "title": "Tolerance Days",
      "type": "array"
    },
    "narrative": {
      "title": "Narrative",
      "type": "string"
    },
    "allowed_actions": {
      "items": {
        "type": "string"
      },
      "title": "Allowed Actions",
      "type": "array"
    },
    "resource_categories": {
      "items": {
        "type": "string"
      },
      "title": "Resource Categories",
      "type": "array"
    },
    "is_demo": {
      "default": false,
      "title": "Is Demo",
      "type": "boolean"
    },
    "generated_at": {
      "format": "date-time",
      "title": "Generated At",
      "type": "string"
    }
  },
  "required": [
    "card_id",
    "program",
    "kind",
    "status",
    "source",
    "patient_pseudonym",
    "period_start",
    "period_end",
    "headline",
    "narrative",
    "generated_at"
  ],
  "title": "SignalCard",
  "type": "object"
}
```
