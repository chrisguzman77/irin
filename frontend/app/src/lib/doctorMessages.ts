import "./doctorEcho.js";
import type { components } from "../types/contracts";

// Doctor messages (justin.md R4, invariant 8). Shapes from contracts.py:
// DoctorMessage in the snapshot's pending_doctor_messages, updated by
// doctor_message_received / doctor_message_resolved. Confirm and decline are
// fresh-PIN verbs (lib/freshPin.ts postFresh).
export type DoctorMessage = components["schemas"]["DoctorMessage"];

export const echoDoctorMessage = (m: DoctorMessage, doctorName: string | null) => globalThis.irinDoctorEcho(m, doctorName);

export const messagePath = (id: string, verb: "confirm" | "decline") =>
  `/api/rounds/messages/${encodeURIComponent(id)}/${verb}`;

/** doctor_message_received: add (or replace) a pending message. */
export function addMessage(list: DoctorMessage[] | undefined, payload: Record<string, unknown>): DoctorMessage[] {
  const m = (payload.message ?? payload) as DoctorMessage;
  const cur = list ?? [];
  if (!m || typeof m.message_id !== "string" || (m.status ?? "pending") !== "pending") return cur;
  return [...cur.filter((x) => x.message_id !== m.message_id), m];
}

/** doctor_message_resolved: the Pi says it is settled (confirmed, declined, expired). */
export function removeMessage(list: DoctorMessage[] | undefined, payload: Record<string, unknown>): DoctorMessage[] {
  const id = payload.message_id ?? (payload.message as { message_id?: string } | undefined)?.message_id;
  return (list ?? []).filter((x) => x.message_id !== id);
}
