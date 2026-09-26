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
| program | standing \| step_watch (cards) |
| is_demo | outside the ciphertext; must match the pairing's is_demo or the relay rejects it |

## Relay API v0 (keep identical to docs/plans/chris.md)

- `POST /v0/cards` {recipient_id, sender_id, nonce, ciphertext, source, kind, program, is_demo} (source key). `GET /v0/inbox/{recipient_id}?since=` (bearer).
- `POST /v0/messages` (doctor -> device, sealed, bearer). `GET /v0/device/{device_id}/messages` (source key; the Pi polls). `POST /v0/messages/{id}/resolution` {status} (source key; metadata only).
- `POST /v0/pair` {token, device_pk, is_demo} (source key). `GET /v0/pair/{token}` (public). `POST /v0/pair/{token}/complete` {doctor_pk, doctor_display_name} (public, single use). `POST /v0/pair/{token}/confirm` (source key; issues the bearer). `POST /v0/pair/{id}/revoke` (source key or bearer).
  Pairing shapes (R5, the Pi's side is backend/app/rounds/pairing.py): `POST /v0/pair` body also carries `peer_kind` (doctor | buddy). `GET /v0/pair/{token}` -> `{status: pending | completed | confirmed, doctor_pk?, doctor_display_name?, is_demo, peer_kind}` (404 once expired or unknown). `POST /v0/pair/{token}/complete` -> `{status: "completed"}` (409 if already completed). `POST /v0/pair/{token}/confirm` -> `{pairing_id, doctor_id, bearer_issued: true}` (the bearer itself goes to the browser through `GET /v0/pair/{token}` after confirm, once, as `bearer`). `POST /v0/pair/{id}/revoke` -> `{status: "revoked"}` and the relay forgets the doctor_pk and the bearer. Both sides show `code4` = first 4 bytes of sha256(UTF-8 of device_pk_b64 + doctor_pk_b64 + token_hex) as a big-endian integer, mod 10000, zero-padded; keys are base64 (standard, padded), tokens 32 hex chars, nonces base64. The QR URL is `<inbox or watch page>/pair#token=...&device_pk=...&relay=...`: everything after # never reaches a server.
- Owner pairing (R5+): `POST /v0/device/pairings` {code, device_id, device_url, token, expires_at} (source key). `POST /v0/device/pair` {code, username} -> {device_id, device_url, token} (public). `DELETE /v0/device/pair` (the pairing token, or the source key).
- `POST /v0/spark/new_rx` (demo-only, admin key; no patient identity).
- `POST /v0/resources/request` {doctor_id, category, brand} (bearer): the only thing a pharma-side system would ever see; no patient field.
- `GET /v0/log`: the \"what Impiricus sees\" view (IDs, timestamps, sizes, ciphertext prefixes; never a plaintext field).
- Hub (B3): `POST /v0/hub/listing`, `GET /v0/hub/list`, `POST /v0/hub/claim`, `GET /v0/hub/claim/{id}/script`, `POST /v0/hub/call`, `POST /v0/hub/treating`, `POST /v0/hub/resolve`, `GET /v0/hub/audit`.
- Buddy directory (B3+): `POST /v0/users`, `GET /v0/users/search?username=`, `POST /v0/match`, `POST /v0/match/{id}/accept | decline`.

All routes are 501 stubs in the skeleton; each names its step in `detail`.

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
