import { deviceFetch } from "./api";
import type { components } from "../types/pi";

// The demo panel's controls (backend/app/demo.py, chris.md step 12). Every
// POST is PIN-gated and demo-only: the Pi 404s them in live mode, so nothing
// here can touch live data.
type S = components["schemas"];
export type ScenarioRequest = S["ScenarioRequest"];
export type SpeedRequest = S["SpeedRequest"];
export type PauseRequest = S["PauseRequest"];
export type InjectRequest = S["InjectRequest"];

/** GET /api/demo/scenarios returns a plain dict (no response model). */
export interface ScenarioList {
  scenarios: string[];
  current: string | null;
  speed: number;
  /** whether the replay feed is paused; false when the Pi does not say */
  paused: boolean;
  /** Irin Brain only (R12): card rows change confidence label, never blank */
  brain_only: boolean;
  /** the replay clock (the Pi's naive local time), null when not given */
  clock: string | null;
  /** the scenario companion's kind, null when the scenario has none */
  companion: string | null;
}

export async function listScenarios(base: string): Promise<ScenarioList> {
  const res = await fetch(`${base}/api/demo/scenarios`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${res.status}`);
  const b = (await res.json()) as Partial<ScenarioList>;
  return {
    scenarios: Array.isArray(b.scenarios) ? b.scenarios : [],
    current: typeof b.current === "string" ? b.current : null,
    speed: typeof b.speed === "number" ? b.speed : 1,
    paused: b.paused === true,
    brain_only: b.brain_only === true,
    clock: typeof b.clock === "string" ? b.clock : null,
    companion: typeof b.companion === "string" ? b.companion : null,
  };
}

/** One demo POST: the Pi's JSON answer, or the message saying why not. */
async function postJson<T>(base: string, path: string, body?: unknown): Promise<{ ok: true; body: T } | { ok: false; reason: string }> {
  const res = await deviceFetch(base, `/api/demo/${path}`, {
    method: "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let b: unknown = null;
  try {
    b = await res.json();
  } catch {
    /* no body */
  }
  if (res.ok) return { ok: true, body: b as T };
  const detail = b && typeof (b as { detail?: unknown }).detail === "string" ? (b as { detail: string }).detail : "";
  return { ok: false, reason: detail ? `Your Irin said (${res.status}): ${detail}` : `Your Irin refused it (${res.status}).` };
}

/** One demo POST; resolves to an error message, or "" on success. */
async function post(base: string, path: string, body?: unknown): Promise<string> {
  const r = await postJson(base, path, body);
  return r.ok ? "" : r.reason;
}

export const playScenario = (base: string, name: string) => post(base, "scenario", { name } satisfies ScenarioRequest);
export const setSpeed = (base: string, speed: number) => post(base, "speed", { speed } satisfies SpeedRequest);
export const setPaused = (base: string, paused: boolean) => post(base, "pause", { paused } satisfies PauseRequest);
export const injectLow = (base: string, glucose_mgdl: number) =>
  post(base, "inject_low", { glucose_mgdl, trend: "SingleDown" } satisfies InjectRequest);
export const basalNudge = (base: string) => post(base, "basal_time");

// R12 (chris/r12-seek): the seek and the sponsor-tier controls, all demo-only.
/** {step, day} jumps to day D of plan step N; {day} to day D of the scenario;
 * the clock lands at 09:00 of that day. 409 when the replay is already past it. */
export type SeekTarget = { step: number; day: number } | { day: number } | { date: string };
export interface SeekResult {
  scenario: string;
  clock: string;
  seeded: Record<string, number>;
  nights_built: number;
  mornings_evaluated: number;
  to: string;
}
export const seek = (base: string, target: SeekTarget) => postJson<SeekResult>(base, "seek", target);
export const setBrainOnly = (base: string, brain_only: boolean) =>
  postJson<{ brain_only: boolean }>(base, "brain_only", { brain_only });
/** A pending plan_create from "Impiricus Spark (simulated)": the doctor-message
 * takeover confirms it with a fresh PIN, which starts the watch. */
export const sparkOffer = (base: string) =>
  postJson<{ message_id: string; plan_id: string; status: string }>(base, "spark_offer");
/** 404 until the Night Buddy tier lands. */
export const buddyRung = (base: string) => postJson<{ status: string }>(base, "buddy_rung");

/** R14(c) Brain versus Bedside: the real engine's current card (the Step Watch
 * card, else the Basal Check), sealed as the bedside device, as Brain only, or
 * both side by side. Brain only changes confidence labels, never a row. */
export type SendMode = "bedside" | "brain" | "both";
export interface SentCard { card_id: string; kind: string; program: string; status: string; recipients: string[]; is_demo: boolean }
export const sendEvaluatedCard = (base: string, mode: Exclude<SendMode, "bedside">) =>
  postJson<{ mode: SendMode; cards: SentCard[] }>(base, "send_card", { mode });

/** GET /api/rounds/evaluations: the latest Standing evaluation per kind and the
 * noise budget's verdict (a green goes to the digest and never interrupts, so
 * the panel is where the presenter shows it). */
export interface Evaluation { kind: string; status: string; headline: string; budget: string | null; sent: unknown }
export async function latestEvaluations(base: string): Promise<Evaluation[]> {
  const res = await fetch(`${base}/api/rounds/evaluations`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${res.status}`);
  const b = (await res.json()) as Record<string, Partial<Evaluation> | null>;
  return Object.entries(b ?? {})
    .filter(([, e]) => e && typeof e === "object")
    .map(([k, e]) => ({
      kind: typeof e!.kind === "string" ? e!.kind : k,
      status: typeof e!.status === "string" ? e!.status : "",
      headline: typeof e!.headline === "string" ? e!.headline : "",
      budget: typeof e!.budget === "string" ? e!.budget : null,
      sent: e!.sent ?? null,
    }));
}
