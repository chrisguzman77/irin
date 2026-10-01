-- 007_phone_accounts.sql (George's lane, stepped in by Chris; 001-006 are frozen)
-- Phone-only accounts, Phase 1 (relay/README.md "Phone-only accounts"): a phone
-- with no Irin verifies its CGM feed once through POST /v1/accounts/verify. The
-- feed URL and token are stored encrypted (nacl SecretBox under sha256(RELAY_KEY));
-- the relay learns only the boolean (invariant 20). dash_token_hash is the
-- SHA-256 of the dashboard token GET /v1/dash/{name} accepts; the token itself
-- is returned once, and a re-verify replaces the hash so the old token stops
-- working. Plain table, no hypertable. No glucose value is ever stored here.

CREATE TABLE IF NOT EXISTS phone_accounts (
    user_id              text PRIMARY KEY,
    nightscout_url_enc   bytea,
    nightscout_token_enc bytea,
    verified_at          timestamptz,
    dash_token_hash      text UNIQUE,
    created_at           timestamptz DEFAULT now()
);
