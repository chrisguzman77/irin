import { AccountRequired, getAccount, markVerified, relayFetch, relayReason, type AccountResult } from "./account";
import { readMatchCard, type BuddyProfile, type HubClaim, type HubRow, type MatchCard } from "./buddy";

// Phone-only accounts, Phase 1 (relay/README.md "Phone-only accounts"): the
// buddy directory straight from the relay, for a phone with no Irin paired.
// The Pi path (lib/buddy.ts) is untouched; this file speaks the same shapes
// the Pi proxies today, with the account's bearer (lib/account.ts). Nothing
// here carries a glucose value, a place, or a contact (invariant 15): the
// relay would 422 it anyway.

// Minimal local shapes for the relay's user routes. Not added to
// types/relay.d.ts: it is regenerated from the relay's live /openapi.json, and
// the relay side of this section is being built in parallel.
export interface Me {
  user_id: string;
  username: string;
  first_name: string;
  languages: string[];
  timezones: string[];
  availability: BuddyProfile["availability"];
  optins: BuddyProfile["optins"];
  cgm_verified: boolean;
  is_demo: boolean;
  phone: boolean;
  device_linked: boolean;
}
const OFF: BuddyProfile["optins"] = { have_buddy: false, be_watcher: false, hub_watchable: false, hub_volunteer: false };

const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);
const obj = (v: unknown): Record<string, unknown> | null =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);

function readMe(v: unknown): Me {
  const o = obj(v) ?? {};
  const opt = obj(o.optins) ?? {};
  return {
    user_id: str(o.user_id) ?? "",
    username: str(o.username) ?? "",
    first_name: str(o.first_name) ?? "",
    languages: strings(o.languages),
    timezones: strings(o.timezones),
    availability: (Array.isArray(o.availability) ? o.availability : []).flatMap((s) => {
      const r = obj(s);
      return r && typeof r.weekday === "number" && str(r.start) && str(r.end) ? [{ weekday: r.weekday, start: str(r.start)!, end: str(r.end)! }] : [];
    }),
    optins: {
      have_buddy: opt.have_buddy === true,
      be_watcher: opt.be_watcher === true,
      hub_watchable: opt.hub_watchable === true,
      hub_volunteer: opt.hub_volunteer === true,
    },
    cgm_verified: o.cgm_verified === true,
    is_demo: o.is_demo === true,
    phone: o.phone === true,
    device_linked: o.device_linked === true,
  };
}

/** The sign-up body a Me reads back as (PUT /v0/users/me takes all of it). */
export const profileOf = (me: Me): BuddyProfile => ({
  username: me.username,
  first_name: me.first_name,
  languages: me.languages,
  timezones: me.timezones,
  availability: me.availability,
  optins: { ...OFF, ...me.optins },
});

/** Runs one relay call; a lost connection is a reason, a 401 has already
 * cleared the account (the tab flips to sign-up) and reads as one too. */
async function call<T>(f: () => Promise<Response>, read: (res: Response) => Promise<AccountResult<T>>): Promise<AccountResult<T>> {
  let res: Response;
  try {
    res = await f();
  } catch (e) {
    if (e instanceof AccountRequired) return { ok: false, reason: "Irin's relay no longer knows this phone's account. Sign up again." };
    return { ok: false, reason: "Could not reach Irin's relay." };
  }
  if (!res.ok) return { ok: false, reason: await relayReason(res) };
  return read(res);
}

/** GET /v0/users/me; a cgm_verified answer marks the stored account verified
 * (a linked Irin may have verified the feed itself). */
export function getMe(): Promise<AccountResult<Me>> {
  return call(() => relayFetch("/v0/users/me"), async (res) => {
    const me = readMe(await res.json());
    if (me.cgm_verified) markVerified();
    return { ok: true, value: me };
  });
}

/** PUT /v0/users/me with the full sign-up body (profile and opt-in edits). */
export function updateMe(p: BuddyProfile): Promise<AccountResult<Me>> {
  return call(
    () => relayFetch("/v0/users/me", { method: "PUT", body: JSON.stringify(p) }),
    async (res) => ({ ok: true, value: readMe(await res.json()) }),
  );
}

const hours = (h: number) => Math.round(h * 10) / 10;
const listOf = (xs: string[]) => (xs.length <= 1 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`);

/** The intro and why lines, a deterministic template from hours_covered,
 * mirror, and shared_languages (no Muse on this path, per the pinned
 * section): never a glucose number, never a place. */
export function templateLines(c: MatchCard): { intro: string; why: string } {
  const h = hours(c.hours_covered);
  const intro =
    h > 0
      ? `${c.first_name} is awake for about ${h} ${h === 1 ? "hour" : "hours"} of your night.`
      : `${c.first_name} keeps hours close to yours.`;
  const why = [
    c.mirror ? "lives in a mirror time zone, awake while you sleep" : "keeps hours close to yours",
    c.shared_languages.length > 0 ? `you both speak ${listOf(c.shared_languages)}` : null,
  ].filter((x): x is string => !!x);
  return { intro, why: `Why ${c.first_name}: ${why.join("; ")}.` };
}

/** POST /v0/match {} -> up to three offers, each with the template lines.
 * The relay answers 403 (its detail is shown) until the account is CGM-verified
 * with "Have a buddy" on; candidates are only those who selected you. */
export function findMatches(): Promise<AccountResult<MatchCard[]>> {
  return call(
    () => relayFetch("/v0/match", { method: "POST", body: "{}" }),
    async (res) => {
      const body = await res.json();
      const rows = Array.isArray(body) ? body : Array.isArray(obj(body)?.matches) ? (obj(body)!.matches as unknown[]) : [];
      return {
        ok: true,
        value: rows.flatMap((r) => {
          const c = readMatchCard(r);
          return c ? [{ ...c, ...templateLines(c) }] : [];
        }),
      };
    },
  );
}

/** POST /v0/match/{id}/accept | decline -> the match with its status. */
export function answerMatch(matchId: string, verb: "accept" | "decline"): Promise<AccountResult<{ match_id: string; status: string }>> {
  return call(
    () => relayFetch(`/v0/match/${encodeURIComponent(matchId)}/${verb}`, { method: "POST" }),
    async (res) => {
      const o = obj(await res.json()) ?? {};
      return { ok: true, value: { match_id: str(o.match_id) ?? matchId, status: str(o.status) ?? "offered" } };
    },
  );
}

/** GET /v0/users/hub -> the same rows the Pi proxies (lib/buddy.ts HubRow). */
export function getHub(): Promise<AccountResult<HubRow[]>> {
  return call(() => relayFetch("/v0/users/hub"), async (res) => {
    const body = await res.json();
    return {
      ok: true,
      value: (Array.isArray(body) ? body : []).flatMap((r) => {
        const o = obj(r);
        if (!o || !str(o.listing_id)) return [];
        return [{
          listing_id: str(o.listing_id)!,
          first_name: str(o.first_name) ?? "Someone",
          languages: strings(o.languages),
          elapsed_min: typeof o.elapsed_min === "number" ? o.elapsed_min : 0,
          urgency: typeof o.urgency === "number" ? o.urgency : 0,
          // anything but an explicit device_confirmed reads as unconfirmed (never overstate confidence)
          confidence: o.confidence === "device_confirmed" ? "device_confirmed" : "unconfirmed",
          sample: o.sample === true,
        }];
      }),
    };
  });
}

/** POST /v0/users/hub/{listing_id}/claim -> {claim_id, expires_at, script: {steps}};
 * the script exists only in this reply, while the claim is live (invariant 14). */
export function claimHub(listingId: string): Promise<AccountResult<HubClaim>> {
  return call(
    () => relayFetch(`/v0/users/hub/${encodeURIComponent(listingId)}/claim`, { method: "POST" }),
    async (res) => {
      const o = obj(await res.json()) ?? {};
      const steps = obj(o.script)?.steps;
      return { ok: true, value: { claim_id: str(o.claim_id) ?? "", expires_at: str(o.expires_at), steps: strings(steps) } };
    },
  );
}

// The accepted match, kept on this phone: the relay exposes no "my matches"
// read for a bearer (the Pi learns it from its device poll), so the card the
// user accepted here is the My buddy card until Phase 2. Keyed to the account
// so a card never outlives the account that accepted it.
const MATCH_KEY = "irin.phoneMatch";

export function getPhoneMatch(): MatchCard | null {
  const account = getAccount();
  if (!account) return null;
  try {
    const raw = localStorage.getItem(MATCH_KEY);
    if (!raw) return null;
    const p = JSON.parse(raw) as { user_id?: unknown; card?: unknown };
    if (p.user_id !== account.user_id) return null;
    const c = readMatchCard(p.card);
    return c ? { ...c, ...templateLines(c) } : null;
  } catch {
    return null;
  }
}

export function setPhoneMatch(card: MatchCard | null): void {
  const account = getAccount();
  try {
    if (card && account) localStorage.setItem(MATCH_KEY, JSON.stringify({ user_id: account.user_id, card }));
    else localStorage.removeItem(MATCH_KEY);
  } catch {
    /* storage blocked: the screen's state still holds it */
  }
}
