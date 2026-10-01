import { useSyncExternalStore } from "react";
import { CLOUD_URL, RELAY_URL } from "../config";
import type { BuddyProfile } from "./buddy";

// Phone-only accounts, Phase 1 (relay/README.md "Phone-only accounts",
// PINNED 2026-10-01): a phone with no Irin signs up on the relay, verifies
// its CGM feed once through Irin Cloud, and is matched and volunteers like a
// device user. The relay hands back {user_id, user_bearer} exactly once (no
// password, no recovery: a phone that loses this signs up again); the cloud
// hands back a dashboard_token once. Kept in localStorage like lib/owner.ts
// so the account survives a closed tab. The bearer travels only in the
// Authorization header (relayFetch) and the verify body, never in a URL.
const KEY = "irin.account";
const EVENT = "irin:account";

export interface Account {
  user_id: string;
  user_bearer: string;
  username: string;
  verified: boolean;
  dashboard_token: string | null;
}

// Minimal local shapes for the three Phase 1 routes. Not added to
// types/relay.d.ts or a cloud types file: those are regenerated from the live
// /openapi.json, and the relay and cloud sides are being built in parallel
// against the same README section, so the generators have nothing to read yet.
interface PhoneSignUpResponse {
  user_id: string;
  user_bearer: string;
}
interface VerifyResponse {
  user_id: string;
  verified: true;
  dashboard_token: string;
}
interface LinkResponse {
  user_id: string;
  device_linked: true;
}

// useSyncExternalStore compares snapshots with Object.is (see lib/owner.ts):
// cache keyed on the raw string so the same contents yield the same reference.
let cachedRaw: string | null = null;
let cachedValue: Account | null = null;
let cachedOnce = false;

function read(): Account | null {
  let raw: string | null;
  try {
    raw = localStorage.getItem(KEY);
  } catch {
    raw = null;
  }
  if (cachedOnce && raw === cachedRaw) return cachedValue;
  cachedRaw = raw;
  cachedOnce = true;
  if (!raw) {
    cachedValue = null;
    return cachedValue;
  }
  try {
    const p = JSON.parse(raw) as Partial<Account>;
    cachedValue =
      typeof p.user_id === "string" && typeof p.user_bearer === "string" && typeof p.username === "string"
        ? {
            user_id: p.user_id,
            user_bearer: p.user_bearer,
            username: p.username,
            verified: p.verified === true,
            dashboard_token: typeof p.dashboard_token === "string" ? p.dashboard_token : null,
          }
        : null;
  } catch {
    cachedValue = null;
  }
  return cachedValue;
}

export function getAccount(): Account | null {
  return read();
}

export function setAccount(a: Account): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(a));
  } catch {
    /* private mode: the account lives for this page only */
  }
  window.dispatchEvent(new Event(EVENT));
}

export function clearAccount(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* nothing stored */
  }
  window.dispatchEvent(new Event(EVENT));
}

function subscribe(cb: () => void) {
  window.addEventListener(EVENT, cb);
  return () => window.removeEventListener(EVENT, cb);
}

/** Reactive read so the Buddy and My Irin tabs flip live when the account changes. */
export function useAccount(): Account | null {
  return useSyncExternalStore(subscribe, getAccount);
}

/** The relay (or the cloud) no longer recognizes this phone's bearer; the account is cleared. */
export class AccountRequired extends Error {
  constructor() {
    super("sign up again in Irin Buddy");
  }
}

export type AccountResult<T> = { ok: true; value: T } | { ok: false; reason: string };

async function detailOf(res: Response): Promise<string | null> {
  try {
    const b = await res.json();
    return b && typeof b.detail === "string" ? b.detail : null;
  } catch {
    return null;
  }
}

/** A refusal as the user reads it; a 403 / 404 / 409 / 422 detail is written for them. */
export async function relayReason(res: Response, who = "Irin's relay"): Promise<string> {
  const detail = await detailOf(res);
  if (res.status === 429) return "Too many tries from this network. Wait a minute and try again.";
  if (detail && (res.status === 403 || res.status === 404 || res.status === 409 || res.status === 422)) return detail;
  return detail ? `${who} said (${res.status}): ${detail}` : `${who} refused it (${res.status}).`;
}

/** Every relay call made with the account's bearer goes through here: it adds
 * `Authorization: Bearer <user_bearer>` and, on a 401, clears the account and
 * throws AccountRequired (the tab flips back to sign-up by itself). A network
 * failure throws like fetch does. */
export async function relayFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const account = getAccount();
  if (!account) throw new AccountRequired();
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${account.user_bearer}`);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const res = await fetch(RELAY_URL + path, { ...init, headers });
  if (res.status === 401) {
    clearAccount();
    throw new AccountRequired();
  }
  return res;
}

/** POST {RELAY_URL}/v0/users/phone with the sign-up body (the POST /v0/users
 * fields without cgm_verified and is_demo, which the relay sets) -> stores the
 * account, unverified. */
export async function signUpPhone(profile: BuddyProfile): Promise<AccountResult<Account>> {
  let res: Response;
  try {
    res = await fetch(`${RELAY_URL}/v0/users/phone`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(profile),
    });
  } catch {
    return { ok: false, reason: "Could not reach Irin's relay. Check your connection and try again." };
  }
  if (!res.ok) return { ok: false, reason: await relayReason(res) };
  const body = (await res.json()) as PhoneSignUpResponse;
  if (typeof body?.user_id !== "string" || typeof body?.user_bearer !== "string")
    return { ok: false, reason: "Irin's relay answered without an account." };
  const account: Account = { user_id: body.user_id, user_bearer: body.user_bearer, username: profile.username, verified: false, dashboard_token: null };
  setAccount(account);
  return { ok: true, value: account };
}

/** POST {CLOUD_URL}/v1/accounts/verify {user_id, user_bearer, nightscout_url,
 * nightscout_token}. The cloud reads one entry from the feed and discards it;
 * the URL and token stay in the cloud, never on the relay (invariant 20). A
 * 422's detail ("feed unreachable", "feed refused the token", "no reading in
 * the last 15 minutes") is shown verbatim; success stores verified and the
 * dashboard token. */
export async function verifyAccount(nightscoutUrl: string, nightscoutToken: string): Promise<AccountResult<Account>> {
  const account = getAccount();
  if (!account) return { ok: false, reason: "Sign up first." };
  let res: Response;
  try {
    res = await fetch(`${CLOUD_URL}/v1/accounts/verify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: account.user_id, user_bearer: account.user_bearer, nightscout_url: nightscoutUrl, nightscout_token: nightscoutToken }),
    });
  } catch {
    return { ok: false, reason: "Could not reach Irin Cloud. Check your connection and try again." };
  }
  if (res.status === 401) {
    clearAccount();
    return { ok: false, reason: "Irin Cloud did not recognize this phone's account. Sign up again." };
  }
  if (!res.ok) return { ok: false, reason: await relayReason(res, "Irin Cloud") };
  const body = (await res.json()) as VerifyResponse;
  if (typeof body?.dashboard_token !== "string") return { ok: false, reason: "Irin Cloud answered without a dashboard token." };
  const next: Account = { ...account, verified: true, dashboard_token: body.dashboard_token };
  setAccount(next);
  return { ok: true, value: next };
}

/** Mark the account verified without a new dashboard token (GET /v0/users/me
 * says cgm_verified after a linked Irin verified the feed itself). */
export function markVerified(): void {
  const account = getAccount();
  if (account && !account.verified) setAccount({ ...account, verified: true });
}

/** POST {RELAY_URL}/v0/users/link {device_id, owner_token} right after an
 * owner pairing lands: the phone's account and the Irin's directory document
 * become one. The relay answers with the surviving user_id (the device's,
 * when the phone adopts it), which is stored; a 409 ("both profiles already
 * have buddies; disconnect one first") is surfaced and both stay as they are. */
export async function linkAccount(deviceId: string, ownerToken: string): Promise<AccountResult<null>> {
  let res: Response;
  try {
    res = await relayFetch("/v0/users/link", { method: "POST", body: JSON.stringify({ device_id: deviceId, owner_token: ownerToken }) });
  } catch (e) {
    if (e instanceof AccountRequired) return { ok: false, reason: "Irin's relay no longer knows this phone's buddy account; sign up again in Irin Buddy." };
    return { ok: false, reason: "Paired, but Irin's relay could not be reached to link your buddy account." };
  }
  if (!res.ok) return { ok: false, reason: await relayReason(res) };
  const body = (await res.json()) as LinkResponse;
  const account = getAccount();
  if (account && typeof body?.user_id === "string") setAccount({ ...account, user_id: body.user_id });
  return { ok: true, value: null };
}
