import { deviceFetch } from "./api";
import type { components } from "../types/pi";

// Morning reports (justin.md step 7), stored by the Pi (backend step 11).
export type MorningReport = components["schemas"]["MorningReport"];

/** The stats reports.py compute_stats writes. MorningReport.stats is a plain
 * dict in contracts.py, so nothing is generated for it: read defensively and
 * show "—" for anything missing (request to Chris: a typed stats model). */
export interface ReportStats {
  readings?: number;
  coverage_pct?: number;
  low_mgdl?: number | null;
  low_at?: string | null;
  high_mgdl?: number | null;
  high_at?: string | null;
  tir_pct?: number | null;
  tbr_pct?: number | null;
  tar_pct?: number | null;
  minutes_below_70?: number;
  carbs_g?: number;
  insulin_units?: number;
}

export const statsOf = (r: MorningReport): ReportStats => (r.stats ?? {}) as ReportStats;

/** Below this much sensor coverage the night is partly missing; say so. */
export const PARTIAL_COVERAGE_PCT = 85;

async function get<T>(base: string, path: string): Promise<T> {
  const res = await deviceFetch(base, path, { cache: "no-store" });
  if (!res.ok) throw new Error(`${res.status}`);
  return (await res.json()) as T;
}

export const listReports = (base: string, limit = 30) =>
  get<MorningReport[]>(base, `/api/reports?limit=${limit}`);

/** The graph needs the owner bearer, which an <img src> cannot carry: fetch
 * it and hand back an object URL (the caller revokes it). null = no graph. */
export async function fetchGraph(base: string, r: MorningReport): Promise<string | null> {
  if (!r.graph_png_path) return null;
  const res = await deviceFetch(base, `/api/reports/${encodeURIComponent(r.night_date)}/graph.png`);
  if (!res.ok) throw new Error(`${res.status}`);
  return URL.createObjectURL(await res.blob());
}
