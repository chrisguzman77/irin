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
export type DashBody = DashBase & { rows?: unknown[] } & Partial<UnderTheHood>;

export type DashResult =
  | { state: "ok"; body: DashBody }
  | { state: "not_yet"; detail: string } // 501: the cloud names the step that will serve it
  | { state: "no_access" } // 401/403, or 503 while the cloud has no owner token set
  | { state: "error"; detail: string };

export async function fetchDash(name: DashName, days: number, demo: boolean, bearer: string | null): Promise<DashResult> {
  let res: Response;
  try {
    res = await fetch(`${CLOUD_URL}/v1/dash/${name}?days=${days}${demo ? "&demo=true" : ""}`, {
      headers: bearer ? { Authorization: `Bearer ${bearer}` } : {},
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

/** The owner's dashboard token. How the app receives it is not pinned yet
 * (the A2 owner pairing): until then there is none, and every panel says so. */
export function ownerBearer(): string | null {
  return null;
}
