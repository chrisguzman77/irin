import { deviceFetch } from "./api";
import type { Settings, StateSnapshot } from "./contracts";

// Night Buddy B1 (justin.md): the patient side. Everything here renders from
// the snapshot's buddy_state = {link, open_alert, treating, morning_line}
// (contracts.StateSnapshot.buddy_state is an untyped dict, so it is read
// defensively) plus treating_set {since, expires_at}. No glucose value, place,
// or phone number is ever shown here (invariant 15).

/** buddy_state.link = {first_name, mode, peer_id} (backend/app/buddy/rung.py) */
export interface BuddyLink {
  /** the pairing id, for POST /api/pair/{peer_id}/revoke (peer_kind = buddy) */
  peer_id: string | null;
  first_name: string;
  /** twin: same sleep hours; mirror: awake while the other sleeps */
  mode: "twin" | "mirror" | null;
}
export interface Treating {
  since: string | null;
  expires_at: string | null;
}
export interface BuddyState {
  link: BuddyLink | null;
  /** an alert is open while this is set: the treating button shows */
  openAlert: Record<string, unknown> | null;
  treating: Treating | null;
  morningLine: string | null;
  /** the T+20 emergency-script step fired: {at, text} */
  emergency: { at: string | null; text: string } | null;
  /** B3+ directory: my matches, from buddy_state.matches and hub_update "match" */
  matches: MatchState[];
  /** Buddy v3 (relay/README.md, PINNED): buddy_state.my_buddy = the stored
   * MatchOffer of this world's accepted match, or null. Not in src/types yet
   * (buddy_state is an untyped dict), so it is read with readMatchCard. */
  myBuddy: MatchCard | null;
}
/** buddy_state.matches[i]; pair_url is the other side's watcher /pair link, once both accepted */
export interface MatchState {
  match_id: string;
  /** null until the Pi's snapshot names it (hub_update "match" carries no name) */
  first_name: string | null;
  status: "offered" | "accepted" | "declined" | string;
  pair_url: string | null;
  /** the other side is a seeded sample profile (relay "Buddy onboarding v2"): it never sends a watch link */
  sample: boolean;
}

const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);
const obj = (v: unknown): Record<string, unknown> | null =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;

export function readBuddyState(snap: StateSnapshot | null): BuddyState {
  const b = obj(snap?.buddy_state) ?? {};
  const l = obj(b.link);
  const mode = str(l?.mode);
  const t = obj(b.treating);
  const em = obj(b.emergency);
  return {
    link: l
      ? {
          peer_id: str(l.peer_id),
          first_name: str(l.first_name) ?? "your buddy",
          mode: mode === "twin" || mode === "mirror" ? mode : null,
        }
      : null,
    openAlert: b.open_alert ? (obj(b.open_alert) ?? {}) : null,
    treating: t ? { since: str(t.since), expires_at: str(t.expires_at) } : null,
    morningLine: str(b.morning_line),
    emergency: em && str(em.text) ? { at: str(em.at), text: str(em.text)! } : null,
    matches: (Array.isArray(b.matches) ? b.matches : []).flatMap((m) => {
      const o = obj(m);
      const id = str(o?.match_id);
      return o && id
        ? [{ match_id: id, first_name: str(o.first_name), status: str(o.status) ?? "offered", pair_url: str(o.pair_url), sample: o.sample === true }]
        : [];
    }),
    myBuddy: readMatchCard(b.my_buddy),
  };
}

/** The buddy WS messages, folded into the snapshot's buddy_state:
 * buddy_alert (payload = open_alert), treating_set {since, expires_at}, and
 * hub_update {event: resolved | emergency | call, ...}; a resolved that
 * carries `line` is the close-out, not a second resolve; morning {line,
 * night_date} is the night's "all quiet" line, written after the ledger row. */
export function applyBuddyMessage(snap: StateSnapshot, type: string, p: Record<string, unknown>): StateSnapshot {
  const b = { ...(snap.buddy_state ?? {}) };
  if (type === "buddy_alert") b.open_alert = p;
  else if (type === "treating_set") b.treating = p;
  else if (type === "hub_update") {
    if (p.event === "resolved" && typeof p.line === "string") {
      // B5: the close-out follows the resolve (up to 20 s later) for the SAME
      // listing: an update, never a second resolve. It is that night's line.
      b.morning_line = p.line;
    } else if (p.event === "resolved") {
      b.open_alert = null;
      b.treating = null;
      b.emergency = null;
      b.last_call = null;
    } else if (p.event === "morning" && typeof p.line === "string") b.morning_line = p.line;
    else if (p.event === "emergency") b.emergency = { at: p.at ?? null, text: p.text };
    else if (p.event === "call") b.last_call = { chimed: p.chimed === true };
    else if (p.event === "match" && typeof p.match_id === "string") {
      // B3+: a match changed status (or got its pair_url); upsert by match_id
      const prev = (Array.isArray(b.matches) ? b.matches : []) as Record<string, unknown>[];
      const old = prev.find((m) => m?.match_id === p.match_id);
      const row: Record<string, unknown> = { ...(old ?? {}), match_id: p.match_id, status: p.status ?? old?.status, pair_url: p.pair_url ?? old?.pair_url ?? null };
      if (typeof p.first_name === "string") row.first_name = p.first_name;
      if (typeof p.sample === "boolean") row.sample = p.sample;
      b.matches = old ? prev.map((m) => (m === old ? row : m)) : [...prev, row];
    }
    else return snap;
  } else return snap;
  return { ...snap, buddy_state: b };
}

/** hub_update "call" since the alert opened (this page's own note, not a Pi field). */
export const buddyCalled = (snap: StateSnapshot | null) => !!obj(snap?.buddy_state)?.last_call;

/** The ONE press (invariant 17): no confirmation step before or after. */
export async function postTreating(base: string): Promise<{ ok: true } | { ok: false; reason: string }> {
  const res = await deviceFetch(base, "/api/buddy/treating", { method: "POST" });
  if (res.ok) return { ok: true };
  if (res.status === 409) return { ok: false, reason: "No buddy alert is open any more." };
  let detail = "";
  try {
    const b = await res.json();
    if (b && typeof b.detail === "string") detail = b.detail;
  } catch {
    /* no body */
  }
  return { ok: false, reason: detail ? `Your Irin said (${res.status}): ${detail}` : `Your Irin refused it (${res.status}).` };
}

/** "02:47" from the Pi's naive local time, read as text */
export const hhmm = (iso: string | null | undefined) => (iso ? iso.slice(11, 16) : "");

// B3+ directory (relay/README.md "Buddy directory"), through the Pi: the Pi
// holds the relay user bearer and verifies the CGM feed itself; the app sends
// profile fields only. No glucose, location, phone, or email field exists here
// (invariant 15): the relay would 422 it anyway.
export interface Slot {
  weekday: number;
  start: string;
  end: string;
}
export interface BuddyProfile {
  username: string;
  first_name: string;
  languages: string[];
  timezones: string[];
  availability: Slot[];
  optins: { have_buddy: boolean; be_watcher: boolean; hub_watchable: boolean; hub_volunteer: boolean };
}
export interface ProfileReply {
  profile: BuddyProfile | null;
  cgm_verified: boolean;
  /** why the feed is not verified, when the Pi says */
  cgm_reason: string | null;
  user_id: string | null;
  is_demo: boolean;
}
export interface MatchCard {
  match_id: string;
  first_name: string;
  hours_covered: number;
  mirror: boolean;
  shared_languages: string[];
  score: number;
  /** relay/Muse text, validated upstream; shown as given */
  intro: string | null;
  why: string | null;
  is_demo: boolean;
  sample: boolean;
}
export type Result<T> = { ok: true; value: T } | { ok: false; reason: string };

export async function reason(res: Response): Promise<string> {
  try {
    const b = await res.json();
    if (b && typeof b.detail === "string")
      // a 403 / 409 / 422 detail is written for the user ("set up the buddy profile first")
      return res.status === 403 || res.status === 409 || res.status === 422 ? b.detail : `Your Irin said (${res.status}): ${b.detail}`;
  } catch {
    /* no body */
  }
  return `Your Irin refused it (${res.status}).`;
}

function readProfileReply(v: unknown): ProfileReply {
  const o = obj(v) ?? {};
  return {
    profile: (obj(o.profile) as BuddyProfile | null) ?? null,
    cgm_verified: o.cgm_verified === true,
    cgm_reason: str(o.cgm_reason),
    user_id: str(o.user_id),
    is_demo: o.is_demo === true,
  };
}

export async function getProfile(base: string): Promise<Result<ProfileReply>> {
  const res = await deviceFetch(base, "/api/buddy/profile", { pinned: true });
  const none: ProfileReply = { profile: null, cgm_verified: false, cgm_reason: null, user_id: null, is_demo: false };
  if (res.status === 404) return { ok: true, value: none };
  if (!res.ok) return { ok: false, reason: await reason(res) };
  const body = await res.json();
  return { ok: true, value: body === null ? none : readProfileReply(body) };
}

export async function saveProfile(base: string, p: BuddyProfile): Promise<Result<ProfileReply>> {
  const res = await deviceFetch(base, "/api/buddy/profile", { method: "POST", body: JSON.stringify(p) });
  return res.ok ? { ok: true, value: readProfileReply(await res.json()) } : { ok: false, reason: await reason(res) };
}

export async function findMatches(base: string): Promise<Result<MatchCard[]>> {
  const res = await deviceFetch(base, "/api/buddy/match", { method: "POST", body: "{}" });
  if (!res.ok) return { ok: false, reason: await reason(res) };
  const body = await res.json();
  const rows = Array.isArray(body) ? body : Array.isArray(obj(body)?.matches) ? (obj(body)!.matches as unknown[]) : [];
  return { ok: true, value: rows.flatMap((r) => readMatchCard(r) ?? []) };
}

/** One MatchOffer (POST /api/buddy/match rows, buddy_state.my_buddy), read defensively. */
export function readMatchCard(r: unknown): MatchCard | null {
  const o = obj(r);
  if (!o || !str(o.match_id)) return null;
  return {
    match_id: str(o.match_id)!,
    first_name: str(o.first_name) ?? "Someone",
    hours_covered: typeof o.hours_covered === "number" ? o.hours_covered : 0,
    mirror: o.mirror === true,
    shared_languages: Array.isArray(o.shared_languages) ? o.shared_languages.filter((x): x is string => typeof x === "string") : [],
    score: typeof o.score === "number" ? o.score : 0,
    intro: str(o.intro),
    why: str(o.why),
    is_demo: o.is_demo === true,
    sample: o.sample === true,
  };
}

export async function answerMatch(base: string, matchId: string, verb: "accept" | "decline"): Promise<Result<{ match_id: string; status: string }>> {
  const res = await deviceFetch(base, `/api/buddy/match/${encodeURIComponent(matchId)}/${verb}`, { method: "POST" });
  if (!res.ok) return { ok: false, reason: await reason(res) };
  const o = obj(await res.json()) ?? {};
  return { ok: true, value: { match_id: str(o.match_id) ?? matchId, status: str(o.status) ?? "offered" } };
}

/** The wizard's opt-ins + emergency script in one POST /api/settings (the Pi merges a partial body);
 * a 403 / 409 / 422 detail comes back as-is. */
export async function saveBuddySettings(base: string, patch: Partial<Settings>): Promise<Result<null>> {
  const res = await deviceFetch(base, "/api/settings", { method: "POST", body: JSON.stringify(patch) });
  return res.ok ? { ok: true, value: null } : { ok: false, reason: await reason(res) };
}

// Buddy v3 hub for app users (relay/README.md, PINNED), through the Pi's
// GET /api/buddy/hub and POST /api/buddy/hub/{listing_id}/claim (require_pin).
// Local types until `npm run types` picks up the Pi's routes. A row never
// carries a glucose value, place, or contact (invariant 15); the script arrives
// only in the claim reply, while the claim is live (invariant 14).
export interface HubRow {
  listing_id: string;
  first_name: string;
  languages: string[];
  elapsed_min: number;
  urgency: number;
  confidence: "device_confirmed" | "unconfirmed";
  sample: boolean;
}
export interface HubClaim {
  claim_id: string;
  expires_at: string | null;
  steps: string[];
}

export async function getHub(base: string): Promise<Result<HubRow[]>> {
  const res = await deviceFetch(base, "/api/buddy/hub", { pinned: true });
  if (!res.ok) return { ok: false, reason: await reason(res) };
  const body = await res.json();
  return {
    ok: true,
    value: (Array.isArray(body) ? body : []).flatMap((r) => {
      const o = obj(r);
      if (!o || !str(o.listing_id)) return [];
      return [{
        listing_id: str(o.listing_id)!,
        first_name: str(o.first_name) ?? "Someone",
        languages: Array.isArray(o.languages) ? o.languages.filter((x): x is string => typeof x === "string") : [],
        elapsed_min: typeof o.elapsed_min === "number" ? o.elapsed_min : 0,
        urgency: typeof o.urgency === "number" ? o.urgency : 0,
        // anything but an explicit device_confirmed reads as unconfirmed (never overstate confidence)
        confidence: o.confidence === "device_confirmed" ? "device_confirmed" : "unconfirmed",
        sample: o.sample === true,
      }];
    }),
  };
}

/** A 409 ("someone else is helping") comes back as its detail. */
export async function claimHub(base: string, listingId: string): Promise<Result<HubClaim>> {
  const res = await deviceFetch(base, `/api/buddy/hub/${encodeURIComponent(listingId)}/claim`, { method: "POST" });
  if (!res.ok) return { ok: false, reason: await reason(res) };
  const o = obj(await res.json()) ?? {};
  const steps = obj(o.script)?.steps;
  return {
    ok: true,
    value: {
      claim_id: str(o.claim_id) ?? "",
      expires_at: str(o.expires_at),
      steps: Array.isArray(steps) ? steps.filter((x): x is string => typeof x === "string") : [],
    },
  };
}
