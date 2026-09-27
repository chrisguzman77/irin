import { deviceFetch } from "./api";
import type { StateSnapshot } from "./contracts";

// Night Buddy B1 (justin.md): the patient side. Everything here renders from
// the snapshot's buddy_state = {link, open_alert, treating, morning_line}
// (contracts.StateSnapshot.buddy_state is an untyped dict, so it is read
// defensively) plus treating_set {since, expires_at}. No glucose value, place,
// or phone number is ever shown here (invariant 15).

export interface BuddyLink {
  /** the pairing id, for POST /api/pair/{id}/revoke (peer_kind = buddy) */
  id: string | null;
  name: string;
  /** twin: same sleep hours; mirror: awake while the other sleeps */
  kind: "twin" | "mirror" | null;
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
}

const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);
const obj = (v: unknown): Record<string, unknown> | null =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;

export function readBuddyState(snap: StateSnapshot | null): BuddyState {
  const b = obj(snap?.buddy_state) ?? {};
  const l = obj(b.link);
  const kind = str(l?.kind) ?? str(l?.match_kind) ?? str(l?.match);
  const t = obj(b.treating);
  return {
    link: l
      ? {
          id: str(l.peer_id) ?? str(l.buddy_id) ?? str(l.doctor_id) ?? str(l.id),
          name: str(l.name) ?? str(l.display_name) ?? str(l.doctor_display_name) ?? "your buddy",
          kind: kind === "twin" || kind === "mirror" ? kind : null,
        }
      : null,
    openAlert: b.open_alert ? (obj(b.open_alert) ?? {}) : null,
    treating: t ? { since: str(t.since), expires_at: str(t.expires_at) } : null,
    morningLine: str(b.morning_line),
  };
}

/** treating_set {since, expires_at} lands in the snapshot's buddy_state. */
export function applyTreatingSet(snap: StateSnapshot, payload: Record<string, unknown>): StateSnapshot {
  return { ...snap, buddy_state: { ...(snap.buddy_state ?? {}), treating: payload } };
}

/** The ONE press (invariant 17): no confirmation step before or after. */
export async function postTreating(base: string): Promise<{ ok: true } | { ok: false; reason: string }> {
  const res = await deviceFetch(base, "/api/buddy/treating", { method: "POST" });
  if (res.ok) return { ok: true };
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
