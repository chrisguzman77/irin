import { useEffect, useState, useSyncExternalStore } from "react";
import { deviceFetch } from "./api";
import type { components } from "../types/contracts";

// Morning recall (justin.md R2; chris.md R11). One card per nocturnal low the
// Pi asks about (at most the two deepest, the Pi decides), four one-tap
// answers, never pre-selected. Answers POST /api/rounds/recall/{low_event_id}
// {answer} with the session PIN. Unanswered at noon = "no answer" on the Pi,
// never "fine"; the card goes away then.
export type LowEvent = components["schemas"]["LowEvent"];
export type RecallAnswer = NonNullable<components["schemas"]["LowEventRecall"]["answer"]>;

export const ANSWERS: [RecallAnswer, string][] = [
  ["felt_and_treated", "I felt it and treated it"],
  ["woke_no_symptoms", "I woke up but felt nothing"],
  ["dont_remember", "I don't remember it"],
  ["was_awake", "I was awake anyway"],
];
export const answerLabel = (a: RecallAnswer) => ANSWERS.find(([k]) => k === a)?.[1] ?? a;

/** Carbs were logged near the low: ask only about symptoms (chris.md R11). */
export const SYMPTOM_ANSWERS: [RecallAnswer, string][] = [
  ["felt_and_treated", "Yes, I felt symptoms"],
  ["woke_no_symptoms", "No symptoms"],
];

/** recall_due's payload is not pinned yet (journal request to Chris): accept a
 * list of LowEvents, {low_events: [...]}, or {recalls: [{low_event}...]}. */
export function lowsFromRecallDue(payload: unknown): LowEvent[] | null {
  const isLow = (x: unknown): x is LowEvent =>
    !!x && typeof x === "object" && typeof (x as LowEvent).low_event_id === "string" && typeof (x as LowEvent).nadir_mgdl === "number";
  const p = payload as Record<string, unknown> | unknown[] | null;
  let list: unknown[] | null = null;
  if (Array.isArray(p)) list = p;
  else if (p && Array.isArray((p as Record<string, unknown>).low_events)) list = (p as { low_events: unknown[] }).low_events;
  else if (p && Array.isArray((p as Record<string, unknown>).recalls))
    list = (p as { recalls: { low_event?: unknown }[] }).recalls.map((r) => r.low_event ?? r);
  if (!list) return null;
  return list.filter(isLow);
}

export async function answerRecall(base: string, lowEventId: string, answer: RecallAnswer): Promise<{ ok: true } | { ok: false; reason: string }> {
  const res = await deviceFetch(base, `/api/rounds/recall/${encodeURIComponent(lowEventId)}`, {
    method: "POST",
    body: JSON.stringify({ answer }),
  });
  if (res.ok) return { ok: true };
  let reason = `Your Irin refused it (${res.status}).`;
  try {
    const b = await res.json();
    if (b && typeof b.detail === "string") reason = `Your Irin refused it: ${b.detail}`;
  } catch {
    /* no body */
  }
  return { ok: false, reason };
}

// The answers the Pi accepted this session, shared by the Live view and the
// Rounds tab (there is no GET for recall records yet: journal request).
const answered = new Map<string, RecallAnswer>();
const EVENT = "irin:recall";
export function markAnswered(id: string, a: RecallAnswer): void {
  answered.set(id, a);
  window.dispatchEvent(new Event(EVENT));
}
let snapshot = new Map(answered);
export function useAnswered(): ReadonlyMap<string, RecallAnswer> {
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

/** The Pi's clock (GET /api/health), polled every 30 s: "now" for the noon
 * cut-off and the curve. Never the last reading's time: readings can stop
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

/** Noon of the morning after the low's night (night_date is the evening date). */
export function noonAfter(nightDate: string): number {
  const [y, m, d] = nightDate.split("-").map(Number);
  return new Date(y, m - 1, d + 1, 12, 0, 0).getTime();
}
