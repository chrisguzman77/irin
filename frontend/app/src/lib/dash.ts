import { CLOUD_URL } from "../config";

// My Irin (justin.md A4 / C4): one fetch of GET {cloud}/v1/dash/{name}?days=
// per chart (owner bearer, cloud/README.md). The names are the README's table;
// the response FIELDS are not pinned yet (dash.py is George's C3), so the tab
// draws nothing from a body it cannot name: it reports what came back.
// Dashboards draw pictures and never decide (invariant 21).
export const DASHBOARDS = [
  { name: "nights", title: "Nights", about: "each night as a tile, colored by its reason code" },
  { name: "tir", title: "Time in range", about: "daily bars with 7- and 30-day lines" },
  { name: "profile", title: "Overnight profile", about: "median by time of night, with the 10-90 band" },
  { name: "lows_heatmap", title: "Where my lows happen", about: "lows by hour of the day" },
  { name: "alarms", title: "Alarms", about: "per week: tiers, escalations, re-arms, time to acknowledge" },
  { name: "near_misses", title: "Near-misses", about: "per week" },
  { name: "basal", title: "Basal", about: "dose timing against the nights" },
  { name: "sensor", title: "Sensor", about: "coverage and gaps" },
  { name: "step_watch", title: "Step Watch", about: "nights against the plan's windows" },
  { name: "buddy", title: "Buddy", about: "buddy events (relay metadata only)" },
  { name: "under_the_hood", title: "Under the hood", about: "storage, compression, last sync" },
] as const;
export type DashName = (typeof DASHBOARDS)[number]["name"];

export type DashResult =
  | { state: "ok"; body: unknown }
  | { state: "not_yet"; detail: string } // 501: the cloud names the step that will serve it
  | { state: "no_access" } // 401/403: needs the owner bearer (A2 pairing)
  | { state: "error"; detail: string };

export async function fetchDash(name: DashName, days: number, bearer: string | null): Promise<DashResult> {
  let res: Response;
  try {
    res = await fetch(`${CLOUD_URL}/v1/dash/${name}?days=${days}`, {
      headers: bearer ? { Authorization: `Bearer ${bearer}` } : {},
      cache: "no-store",
    });
  } catch {
    return { state: "error", detail: "Irin Cloud is unreachable" };
  }
  if (res.status === 401 || res.status === 403) return { state: "no_access" };
  const b = (await res.json().catch(() => null)) as { detail?: unknown } | null;
  if (res.status === 501) return { state: "not_yet", detail: typeof b?.detail === "string" ? b.detail : "not implemented" };
  if (!res.ok) return { state: "error", detail: `Irin Cloud answered ${res.status}` };
  return { state: "ok", body: b };
}
