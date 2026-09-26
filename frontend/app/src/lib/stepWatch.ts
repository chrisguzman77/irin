import { useEffect, useState } from "react";
import { deviceFetch, PinRejected } from "./api";
import { logTreatment } from "./log";
import type { components } from "../types/contracts";

// Step Watch (justin.md R2 stomach strip + weekly shot, R3 step timeline;
// chris.md R10). The watch's summary is plan_state (snapshot + WS plan_state);
// the full plan (steps and dates) is GET /api/rounds/plan (PIN: a
// prescription schedule). The stomach check-in is one tap, POST
// /api/rounds/checkin {gi}; a second tap the same day replaces the first; a
// missing answer is never "fine". The weekly shot is a glp1_dose Treatment
// with the current dose label, echoed and confirmed before it is sent.
export type TitrationPlan = components["schemas"]["TitrationPlan"];
export type Gi = "fine" | "rough" | "cant_eat";

export interface PlanState {
  active: boolean;
  plan_id: string | null;
  drug_label: string | null;
  status: string | null;
  step_index: number | null;
  dose_label: string | null;
  day_in_step: number | null;
  next_step_on: string | null;
  green_weeks: number | null;
  is_demo: boolean;
}

export interface CheckinStatus {
  symptom_check_due: boolean;
  symptom_check_today: Gi | null;
}

export const GI_ANSWERS: [Gi, string][] = [
  ["fine", "Fine"],
  ["rough", "Rough"],
  ["cant_eat", "Can't eat"],
];
export const GRADUATION_GREEN_WEEKS = 4; // step_watch.GRADUATION_GREEN_WEEKS

const str = (v: unknown) => (typeof v === "string" ? v : null);
const int = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : null);

/** plan_state is a plain dict on the Pi: read it defensively ({active: false} = no watch). */
export function readPlanState(x: unknown): PlanState {
  const p = (x ?? {}) as Record<string, unknown>;
  return {
    active: p.active === true,
    plan_id: str(p.plan_id),
    drug_label: str(p.drug_label),
    status: str(p.status),
    step_index: int(p.step_index),
    dose_label: str(p.dose_label),
    day_in_step: int(p.day_in_step),
    next_step_on: str(p.next_step_on),
    green_weeks: int(p.green_weeks),
    is_demo: p.is_demo === true,
  };
}

export function readCheckin(x: unknown): CheckinStatus {
  const c = (x ?? {}) as Record<string, unknown>;
  const today = c.symptom_check_today;
  return {
    symptom_check_due: c.symptom_check_due === true,
    symptom_check_today: today === "fine" || today === "rough" || today === "cant_eat" ? today : null,
  };
}

export async function getPlan(base: string): Promise<TitrationPlan | null> {
  const res = await deviceFetch(base, "/api/rounds/plan", { pinned: true, cache: "no-store" });
  if (!res.ok) return null;
  const b = (await res.json()) as { plan?: TitrationPlan | null };
  return b.plan ?? null;
}

async function reason(res: Response): Promise<string> {
  const b = (await res.json().catch(() => null)) as { detail?: unknown } | null;
  return typeof b?.detail === "string" ? `Your Irin refused it: ${b.detail}` : `Your Irin refused it (${res.status}).`;
}

export async function postCheckin(base: string, gi: Gi): Promise<{ ok: true; status: CheckinStatus } | { ok: false; reason: string }> {
  const res = await deviceFetch(base, "/api/rounds/checkin", { method: "POST", body: JSON.stringify({ gi }) });
  if (res.status === 409) return { ok: false, reason: "No Step Watch is running, so there is nothing to check in." };
  if (!res.ok) return { ok: false, reason: await reason(res) };
  return { ok: true, status: readCheckin(await res.json()) };
}

/** The weekly shot: a glp1_dose entry naming the dose it was, no units (the
 * label is the dose), stamped with the Pi's clock like every log entry. */
export async function logShot(base: string, doseLabel: string): Promise<{ ok: true } | { ok: false; reason: string }> {
  try {
    await logTreatment(base, { kind: "glp1_dose", dose_label: doseLabel, confirmed: true });
    return { ok: true };
  } catch (e) {
    if (e instanceof PinRejected) throw e;
    return { ok: false, reason: `Your Irin refused it: ${e instanceof Error ? e.message : "unknown error"}` };
  }
}

export async function getCheckin(base: string): Promise<CheckinStatus | null> {
  const res = await deviceFetch(base, "/api/rounds/checkin", { pinned: true, cache: "no-store" });
  return res.ok ? readCheckin(await res.json()) : null;
}

const M = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
/** "Mar 2" from a plain calendar date, read as text (never through a timezone). */
export function dayLabel(iso: string | null | undefined): string {
  if (!iso) return "";
  const [, m, d] = iso.slice(0, 10).split("-").map(Number);
  return m ? `${M[m - 1]} ${d}` : "";
}

/** The watch summary for a screen. The snapshot's plan_state is authoritative
 * (and live plan_state messages replace it); while the snapshot does not carry
 * it (contracts.StateSnapshot has active_plan but no plan_state field yet: FOR
 * CHRIS) and a plan is active, the same fields come from GET /api/rounds/plan. */
export function usePlanState(base: string | null, snap: { plan_state?: unknown; active_plan?: TitrationPlan | null } | null): PlanState {
  const fromSnap = snap?.plan_state && typeof (snap.plan_state as { active?: unknown }).active === "boolean" ? readPlanState(snap.plan_state) : null;
  const planKey = snap?.active_plan ? JSON.stringify(snap.active_plan) : "";
  const [fetched, setFetched] = useState<PlanState | null>(null);
  useEffect(() => {
    if (fromSnap || !base || !planKey) {
      setFetched(null);
      return;
    }
    let alive = true;
    deviceFetch(base, "/api/rounds/plan", { pinned: true, cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((b) => alive && setFetched(b ? readPlanState(b) : null))
      .catch(() => {});
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [base, planKey, !!fromSnap]);
  return fromSnap ?? fetched ?? readPlanState(null);
}
