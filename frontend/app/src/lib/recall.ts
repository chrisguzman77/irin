import { useEffect, useState, useSyncExternalStore } from "react";
import { deviceFetch } from "./api";
import type { components } from "../types/contracts";

// Morning recall (justin.md R2; chris.md R11). The Pi asks about at most the
// two deepest nocturnal lows of the night: WS recall_due = {recalls: [{recall,
// low_event, prefill_treated, answer_until}]}, and the same list (open
// questions only) under the snapshot's todays_checkin_status.recalls. Four
// one-tap answers, never pre-selected; prefill_treated (carbs logged near the
// low) asks only about symptoms. POST /api/rounds/recall/{low_event_id}
// {answer} (PIN) returns the LowEventRecall; 409 at or after the morning's
// noon (recorded as "no answer", never "fine"). GET /api/rounds/recalls (PIN,
// this morning by default) lists every question with its answer, so an answer
// shows again after a reload.
export type LowEvent = components["schemas"]["LowEvent"];
export type LowEventRecall = components["schemas"]["LowEventRecall"];
export type RecallAnswer = NonNullable<LowEventRecall["answer"]>;

export interface RecallItem {
  recall: LowEventRecall;
  low_event: LowEvent;
  prefill_treated: boolean;
  /** the Pi's noon of that morning, naive Pi-local ISO: the card closes then */
  answer_until: string;
}

export const ANSWERS: [RecallAnswer, string][] = [
  ["felt_and_treated", "I felt it and treated it"],
  ["woke_no_symptoms", "I woke up but felt nothing"],
  ["dont_remember", "I don't remember it"],
  ["was_awake", "I was awake anyway"],
];
export const answerLabel = (a: RecallAnswer) => ANSWERS.find(([k]) => k === a)?.[1] ?? a;

/** Carbs were logged near the low (prefill_treated): ask only about symptoms (chris.md R11). */
export const SYMPTOM_ANSWERS: [RecallAnswer, string][] = [
  ["felt_and_treated", "Yes, I felt symptoms"],
  ["woke_no_symptoms", "No symptoms"],
];

/** {recalls: [...]} as pinned; an item without its low event is skipped, never guessed. */
export function recallItems(payload: unknown): RecallItem[] {
  const list = (payload as { recalls?: unknown } | null)?.recalls;
  if (!Array.isArray(list)) return [];
  return list.filter(
    (x): x is RecallItem =>
      !!x && typeof x === "object" && typeof (x as RecallItem).low_event?.low_event_id === "string" &&
      typeof (x as RecallItem).answer_until === "string",
  );
}

/** Every question of this morning with its answer (after a reload the answers come from here). */
export async function getRecalls(base: string): Promise<RecallItem[]> {
  const res = await deviceFetch(base, "/api/rounds/recalls", { pinned: true, cache: "no-store" });
  if (!res.ok) return [];
  const b = (await res.json()) as unknown;
  return recallItems(Array.isArray(b) ? { recalls: b } : b);
}

export async function answerRecall(
  base: string,
  lowEventId: string,
  answer: RecallAnswer,
): Promise<{ ok: true; recall: LowEventRecall } | { ok: false; closed?: true; reason: string }> {
  const res = await deviceFetch(base, `/api/rounds/recall/${encodeURIComponent(lowEventId)}`, {
    method: "POST",
    body: JSON.stringify({ answer }),
  });
  if (res.ok) return { ok: true, recall: (await res.json()) as LowEventRecall };
  if (res.status === 409) return { ok: false, closed: true, reason: "It is past noon: this one is recorded as no answer." };
  if (res.status === 404) return { ok: false, reason: "Your Irin no longer has this question." };
  const b = (await res.json().catch(() => null)) as { detail?: unknown } | null;
  return { ok: false, reason: typeof b?.detail === "string" ? `Your Irin refused it: ${b.detail}` : `Your Irin refused it (${res.status}).` };
}

// The answers the Pi holds, shared by the Live view and the Rounds tab: seeded
// from GET /api/rounds/recalls, updated by each accepted POST. CLOSED marks a
// question the Pi refused as past noon (recorded there as "no answer").
export const CLOSED = "closed" as const;
const answered = new Map<string, RecallAnswer | typeof CLOSED>();
const EVENT = "irin:recall";
export function markAnswered(id: string, a: RecallAnswer | typeof CLOSED): void {
  answered.set(id, a);
  window.dispatchEvent(new Event(EVENT));
}
let snapshot = new Map(answered);
export function useAnswered(): ReadonlyMap<string, RecallAnswer | typeof CLOSED> {
  return useSyncExternalStore(
    (cb) => {
      const h = () => {
        snapshot = new Map(answered);
        cb();
      };
      window.addEventListener(EVENT, h);
      return () => window.removeEventListener(EVENT, h);
    },
    () => snapshot,
  );
}

/** The Pi's clock (GET /api/health), polled every 30 s: "now" for closing a
 * card at answer_until. Never the last reading's time: readings can stop
 * (stale) while the clock runs on. null until the first answer. */
export function usePiClock(base: string | null): number | null {
  const [now, setNow] = useState<{ pi: number; at: number } | null>(null);
  const [, tick] = useState(0);
  useEffect(() => {
    if (!base) return;
    let alive = true;
    const poll = () =>
      fetch(`${base}/api/health`, { cache: "no-store" })
        .then((r) => (r.ok ? r.json() : null))
        .then((h: { clock?: string } | null) => {
          const t = h?.clock ? new Date(h.clock).getTime() : NaN;
          if (alive && !Number.isNaN(t)) setNow({ pi: t, at: Date.now() });
        })
        .catch(() => {});
    poll();
    const id = window.setInterval(() => {
      poll();
      tick((n) => n + 1);
    }, 30000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [base]);
  return now ? now.pi : null;
}
