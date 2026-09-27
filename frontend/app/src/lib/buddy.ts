import { deviceFetch } from "./api";
import type { StateSnapshot } from "./contracts";

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
  };
}

/** The buddy WS messages, folded into the snapshot's buddy_state:
 * buddy_alert (payload = open_alert), treating_set {since, expires_at}, and
 * hub_update {event: resolved | emergency | call, ...}. */
export function applyBuddyMessage(snap: StateSnapshot, type: string, p: Record<string, unknown>): StateSnapshot {
  const b = { ...(snap.buddy_state ?? {}) };
  if (type === "buddy_alert") b.open_alert = p;
  else if (type === "treating_set") b.treating = p;
  else if (type === "hub_update") {
    if (p.event === "resolved") {
      b.open_alert = null;
      b.treating = null;
      b.emergency = null;
      b.last_call = null;
    } else if (p.event === "emergency") b.emergency = { at: p.at ?? null, text: p.text };
    else if (p.event === "call") b.last_call = { chimed: p.chimed === true };
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
