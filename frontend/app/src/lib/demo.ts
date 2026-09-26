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
}

export async function listScenarios(base: string): Promise<ScenarioList> {
  const res = await fetch(`${base}/api/demo/scenarios`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${res.status}`);
  const b = (await res.json()) as Partial<ScenarioList>;
  return {
    scenarios: Array.isArray(b.scenarios) ? b.scenarios : [],
    current: typeof b.current === "string" ? b.current : null,
    speed: typeof b.speed === "number" ? b.speed : 1,
  };
}

/** One demo POST; resolves to an error message, or "" on success. */
async function post(base: string, path: string, body?: unknown): Promise<string> {
  const res = await deviceFetch(base, `/api/demo/${path}`, {
    method: "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.ok) return "";
  if (res.status === 404) {
    try {
      const b = await res.json();
      if (b && typeof b.detail === "string") return b.detail;
    } catch {
      /* no body */
    }
  }
  return `Your Irin refused it (${res.status}).`;
}

export const playScenario = (base: string, name: string) => post(base, "scenario", { name } satisfies ScenarioRequest);
export const setSpeed = (base: string, speed: number) => post(base, "speed", { speed } satisfies SpeedRequest);
export const setPaused = (base: string, paused: boolean) => post(base, "pause", { paused } satisfies PauseRequest);
export const injectLow = (base: string, glucose_mgdl: number) =>
  post(base, "inject_low", { glucose_mgdl, trend: "SingleDown" } satisfies InjectRequest);
export const basalNudge = (base: string) => post(base, "basal_time");
