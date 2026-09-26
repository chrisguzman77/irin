import { deviceFetch } from "./api";
import type { components } from "../types/pi";

// Logging (justin.md step 6). Insulin is NEVER sent as confirmed without the
// echo-and-confirm screen having been passed (invariant 2); the backend
// enforces it too.
export type Treatment = components["schemas"]["Treatment"];

// The voice endpoints return plain dicts (no response_model yet, so nothing
// is generated): these are the statuses backend/app/voice.py documents, read
// defensively. Request: Chris adds response models so this becomes generated.
export type VoiceReply =
  | { status: "stored"; stored: Treatment[] }
  | { status: "needs_confirm"; pending_id: string; echo: string; timeout_s?: number }
  | { status: "incomplete"; missing: string[]; ask: string }
  | { status: "error"; error: string }
  | { status: "expired" }
  | { status: "cancelled" };

async function json(res: Response): Promise<unknown> {
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const b = await res.json();
      if (b && typeof b.detail === "string") detail = b.detail;
    } catch {
      /* no body */
    }
    throw new Error(detail);
  }
  return res.json();
}

function asVoice(v: unknown): VoiceReply {
  const r = v as { status?: unknown };
  if (!r || typeof r.status !== "string") throw new Error("unexpected reply from your Irin");
  return r as VoiceReply;
}

export async function sendVoice(base: string, text: string): Promise<VoiceReply> {
  const body = { text: text.slice(0, 200) };
  return asVoice(await json(await deviceFetch(base, "/api/log/voice", { method: "POST", body: JSON.stringify(body) })));
}

export async function confirmVoice(base: string, pendingId: string): Promise<VoiceReply> {
  const path = `/api/log/voice/${encodeURIComponent(pendingId)}/confirm`;
  return asVoice(await json(await deviceFetch(base, path, { method: "POST" })));
}

export async function cancelVoice(base: string, pendingId: string): Promise<VoiceReply> {
  const path = `/api/log/voice/${encodeURIComponent(pendingId)}/cancel`;
  return asVoice(await json(await deviceFetch(base, path, { method: "POST" })));
}

/** The Pi's clock, so an entry lands on the device's timeline (in replay it
 * runs in the scenario's time, not the phone's). */
async function piNow(base: string): Promise<string> {
  const h = (await json(await fetch(`${base}/api/health`))) as { clock?: string };
  if (!h.clock) throw new Error("your Irin did not report its clock");
  return h.clock;
}

/** Structured entry. `confirmed` is true only for entries the user just
 * confirmed on the echo screen (or entries with no insulin at all). */
export async function logTreatment(base: string, t: Omit<Treatment, "timestamp">): Promise<void> {
  const body: Treatment = { ...t, timestamp: await piNow(base) };
  await json(await deviceFetch(base, "/api/log", { method: "POST", body: JSON.stringify(body) }));
}

export async function recentTreatments(base: string, hours = 24): Promise<Treatment[]> {
  return (await json(await fetch(`${base}/api/treatments?hours=${hours}`))) as Treatment[];
}
