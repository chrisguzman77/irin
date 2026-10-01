import { deviceFetch } from "./api";
import type { SignalCard } from "./contracts";
import type { components } from "../types/contracts";

// "What my doctor sees" (justin.md R3): the device's own record of the cards it
// sealed (GET /api/rounds/cards, chris.md R7): {card, status, recipients,
// stored_at}, newest first. The record is a plain dict on the Pi, so it is
// read defensively; the card inside is the generated SignalCard.
export type Pairing = components["schemas"]["Pairing"];

export interface CardRecord {
  card: SignalCard;
  /** sent | unsent (the relay will retry) | no_recipient (no doctor paired) */
  status: string;
  /** doctor_ids the relay accepted it for */
  recipients: string[];
  stored_at: string | null;
}

function asRecord(x: unknown): CardRecord | null {
  const r = x as Partial<CardRecord> | null;
  if (!r || typeof r !== "object" || !r.card || typeof (r.card as SignalCard).card_id !== "string") return null;
  return {
    card: r.card as SignalCard,
    status: typeof r.status === "string" ? r.status : "unknown",
    recipients: Array.isArray(r.recipients) ? r.recipients.filter((d): d is string => typeof d === "string") : [],
    stored_at: typeof r.stored_at === "string" ? r.stored_at : null,
  };
}

export async function listCards(base: string): Promise<CardRecord[]> {
  const res = await deviceFetch(base, `/api/rounds/cards?limit=50`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${res.status}`);
  const rows = (await res.json()) as unknown[];
  return rows.map(asRecord).filter((r): r is CardRecord => r !== null);
}

export type SampleFixture = "signal_card_standing" | "signal_card_step";

/** Demo panel: seal a sample card to every paired demo doctor (PIN, demo only). */
export async function sendSampleCard(base: string, fixture: SampleFixture): Promise<{ ok: true; status: string; recipients: string[] } | { ok: false; reason: string }> {
  const res = await deviceFetch(base, "/api/demo/send_card", { method: "POST", body: JSON.stringify({ fixture }) });
  if (!res.ok) {
    let reason = `Your Irin refused it (${res.status}).`;
    try {
      const b = await res.json();
      if (b && typeof b.detail === "string") reason = b.detail;
    } catch {
      /* no body */
    }
    return { ok: false, reason };
  }
  const b = (await res.json()) as { status?: unknown; recipients?: unknown };
  return {
    ok: true,
    status: typeof b.status === "string" ? b.status : "unknown",
    recipients: Array.isArray(b.recipients) ? (b.recipients as string[]) : [],
  };
}

// Bedside vs phone only (GET /api/rounds/cards/compare): one pair per kind,
// the sealed card as the device recorded it and the same card recomputed
// brain-only (labels change, rows never blank). null = this Pi predates the
// endpoint (404), so the tab falls back to the single cards.
export interface CardPair {
  kind: string;
  bedside: CardRecord;
  brain: SignalCard;
}

export async function compareCards(base: string): Promise<CardPair[] | null> {
  const res = await deviceFetch(base, `/api/rounds/cards/compare`, { cache: "no-store" });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`${res.status}`);
  const rows = (await res.json()) as unknown[];
  return rows.flatMap((x) => {
    const p = x as { kind?: unknown; bedside?: unknown; brain?: unknown } | null;
    const bedside = asRecord(p?.bedside);
    const brain = p?.brain as SignalCard | undefined;
    if (!bedside || !brain || typeof brain.card_id !== "string") return [];
    return [{ kind: typeof p?.kind === "string" ? p.kind : bedside.card.kind, bedside, brain }];
  });
}
