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
| kind | the card kind, or buddy_alert, or the doctor-message kind |
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
- Owner pairing (R5+): `POST /v0/device/pairings` {code, device_id, device_url, token, expires_at} (source key). `POST /v0/device/pair` {code, username} -> {device_id, device_url, token} (public). `DELETE /v0/device/pair` (the pairing token, or the source key).
- `POST /v0/spark/new_rx` (demo-only, admin key; no patient identity).
- `POST /v0/resources/request` {doctor_id, category, brand} (bearer; doctor_id must be the bearer's own, 403 otherwise) -> {request_id, status: handed_off, category, brand, banner, shared_fields: [category, brand]}: the only thing a pharma-side system would ever see; no patient field, no card id, no number. Categories (the same tokens a card's `resource_categories` carries): glucagon_access, gi_side_effect_education, copay_savings, samples_next_pen, bridge_supply, prior_auth_hub, ask_msl; a card with an empty list (off-label use) offers no handoff. `GET /v0/resources` (bearer) lists the doctor's own handoffs, newest first. The banner the inbox shows on the handoff screen is the response's `banner`: "No patient data shared with any manufacturer".
- `GET /v0/log`: the \"what Impiricus sees\" view (routes, device and peer ids, kinds, timestamps, sizes, ciphertext prefixes; never a plaintext field, and no card or message ids).
- Hub (B3): `POST /v0/hub/listing`, `GET /v0/hub/list`, `POST /v0/hub/claim`, `GET /v0/hub/claim/{id}/script`, `POST /v0/hub/call`, `POST /v0/hub/treating`, `POST /v0/hub/resolve`, `GET /v0/hub/audit`. Shapes in "Hub (B3)" below.
- Buddy alerts (B3) need no route of their own: the device posts a sealed envelope to `POST /v0/cards` with `kind: "buddy_alert"`, `program` absent or `"buddy"`, and `recipient_id` = the buddy pairing's doctor_id (a pairing with `peer_kind: buddy`); the watcher page reads it from `GET /v0/inbox/{peer_id}` with its bearer. A buddy pairing accepts buddy_alert and nothing else, and a doctor pairing never accepts buddy_alert (409); `program: buddy` on any other kind, or `standing`/`step_watch` on a buddy_alert, is 422.
- Timestamps: every timestamp the relay returns is ISO 8601 with an explicit offset (`+00:00`; the Mongo client is tz_aware).
- Buddy directory (B3+): `POST /v0/users`, `GET /v0/users/search?username=`, `POST /v0/match`, `POST /v0/match/{id}/accept | decline`.

Pairing, cards, inbox, messages, the device poll, resolutions, and the log are live (R6); resources are live (R13); the hub is live (B3); the owner pairing, Spark, and directory routes are 501 stubs naming their step.

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
