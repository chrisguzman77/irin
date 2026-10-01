import { CLOUD_URL } from "../config";

// My Irin (justin.md A4 / C4): one fetch of GET {cloud}/v1/dash/{name}?days=&demo=
// per chart, with the owner bearer. Every field below is PINNED in
// cloud/README.md ("Dashboard endpoints"); a chart reads only these. demo=true
// reads the replayed device, never drawn as the real one. `empty: true` means
// no data (never draw zeros); `available: false` carries the reason. Timestamps
// are the Pi's naive local time. Dashboards draw pictures and never decide
// (invariant 21).
export const DASHBOARDS = [
  { name: "nights", title: "Nights", about: "each night: its low point, time under 70, and sensor coverage" },
  { name: "tir", title: "Time in range", about: "each day's readings under, in, and over range, with 7- and 30-day lines" },
  { name: "profile", title: "Overnight profile", about: "median by time of day, with the 10-90% band" },
  { name: "lows_heatmap", title: "Where my lows happen", about: "share of readings under 70, by weekday and hour" },
  { name: "alarms", title: "Alarms", about: "per week, by tier" },
  { name: "near_misses", title: "Near-misses", about: "per week" },
  { name: "basal", title: "Basal", about: "when each dose was taken" },
  { name: "sensor", title: "Sensor", about: "each day's coverage (of 288 five-minute slots)" },
  { name: "step_watch", title: "Step Watch", about: "nights against the plan's windows" },
  { name: "buddy", title: "Buddy", about: "buddy events" },
  { name: "under_the_hood", title: "Under the hood", about: "storage, compression, last sync" },
] as const;
export type DashName = (typeof DASHBOARDS)[number]["name"];

/** Every response's envelope (cloud/README.md). */
export interface DashBase {
  name: string;
  device_id: string;
  is_demo: boolean;
  days: number;
  as_of: string | null;
  available: boolean;
  empty: boolean;
  reason?: string;
}
export interface NightRow { night_date: string; readings: number; coverage_pct: number; low_point_mgdl: number | null; minutes_below_70: number; first_reading: string; last_reading: string }
export interface TirRow { day: string; readings: number; avg_mgdl: number; share_under_70: number; share_70_180: number; share_over_180: number; in_range_7d: number | null; in_range_30d: number | null }
export interface ProfileRow { time_of_day: string; p10: number; p50: number; p90: number; readings: number }
export interface HeatRow { weekday: number; hour_of_day: number; under_70: number; readings: number; share_under_70: number }
export interface AlarmRow { week: string; tier: string; alarms: number; escalated: number; rearms: number; mean_ack_min: number | null }
export interface NearMissRow { week: string; near_misses: number }
export interface BasalRow { time: string; minutes_of_day: number; insulin_units: number; confirmed: boolean }
export interface SensorRow { day: string; slots_with_reading: number; gap_minutes: number; coverage_pct: number }
export interface UnderTheHood {
  readings: number; first_reading: string | null; last_reading: string | null; last_sync: string | null;
  readings_table_bytes_all_devices: number | null; compressed_before_bytes: number | null; compressed_after_bytes: number | null;
  compression_ratio: number | null; last_aggregate_refresh: string | null;
}
/** step_watch (cloud/sql/005): the newest confirmed plan and its window. */
export interface StepPlan { plan_id: string; drug_class: string; drug_label: string; status: string; started_at: string; steps: { index: number; dose_label: string; planned_start: string }[] }
export interface StepBaseline { from: string; to: string; low_point_mgdl: number | null; nights: number }
export interface StepNightRow {
  night_date: string; coverage_pct: number; low_point_mgdl: number | null; vs_baseline_mgdl: number | null;
  minutes_below_70: number; tbr_pct: number; near_miss_count: number; level2_count: number;
  reason_codes: string[]; code_source: string;
}
export interface StepCheckin { date: string; gi: "fine" | "rough" | "cant_eat" }
export interface StepInjection { time: string; dose_label: string | null; confirmed: boolean }
export interface StepWatchBody { plan: StepPlan | null; baseline: StepBaseline | null; checkins: StepCheckin[]; injections: StepInjection[] }
export interface BuddyRow {
  week: string; alerts: number; claims: number; calls: number; treating: number; resolved: number;
  alerts_device_confirmed: number; alerts_unconfirmed: number;
}
export type DashBody = DashBase & { rows?: unknown[] } & Partial<UnderTheHood> & Partial<StepWatchBody>;

export type DashResult =
  | { state: "ok"; body: DashBody }
  | { state: "not_yet"; detail: string } // 501: the cloud names the step that will serve it
  | { state: "no_access" } // 401/403, or 503 while the cloud has no owner token set
  | { state: "error"; detail: string };

/** Phone-only accounts, Phase 1 (relay/README.md): the credential is one of
 * three the cloud resolves to a device: an owner pairing token WITH
 * `X-Device-Id` (deviceId), a phone account's dashboard_token, or the pasted
 * admin token (the last two without the header). */
export async function fetchDash(name: DashName, days: number, demo: boolean, bearer: string | null, deviceId: string | null = null): Promise<DashResult> {
  let res: Response;
  try {
    const headers: Record<string, string> = bearer ? { Authorization: `Bearer ${bearer}` } : {};
    if (bearer && deviceId) headers["X-Device-Id"] = deviceId;
    res = await fetch(`${CLOUD_URL}/v1/dash/${name}?days=${days}${demo ? "&demo=true" : ""}`, {
      headers,
      cache: "no-store",
    });
  } catch {
    return { state: "error", detail: "Irin Cloud is unreachable" };
  }
  if (res.status === 401 || res.status === 403 || res.status === 503) return { state: "no_access" };
  const b = (await res.json().catch(() => null)) as (DashBody & { detail?: unknown }) | null;
  if (res.status === 501) return { state: "not_yet", detail: typeof b?.detail === "string" ? b.detail : "not implemented" };
  if (!res.ok || !b) return { state: "error", detail: `Irin Cloud answered ${res.status}` };
  return { state: "ok", body: b };
}

// The owner's dashboard token (the cloud's OWNER_BEARER), typed once in
// Settings and kept in this browser's localStorage, until the A2 owner
// pairing decides how the app receives it. It only reads pictures.
const BEARER_KEY = "irin.owner_bearer";

export function ownerBearer(): string | null {
  try {
    return localStorage.getItem(BEARER_KEY) || null;
  } catch {
    return null;
  }
}

export function setOwnerBearer(token: string | null): void {
  try {
    if (token) localStorage.setItem(BEARER_KEY, token);
    else localStorage.removeItem(BEARER_KEY);
  } catch {
    /* private mode: the token is not kept */
  }
}
