import { deviceFetch } from "./api";
import type { Settings } from "./contracts";

// The alarm sounds shipped in backend/sounds/ (buddy_chime is not an alarm).
export const ALARM_SOUNDS = ["alarm_soft", "alarm_urgent", "chirp"] as const;

/** The one place settings are saved: POST /api/settings with ONLY the fields
 * the user changed (the Pi merges a partial body, so a change made elsewhere
 * in the meantime is not overwritten). "Saved" is shown only once the Pi's
 * settings_change carries the new values, never on the tap. A wrong PIN
 * throws PinRejected (deviceFetch), which sends the app back to the gate. */
export async function saveSettings(
  baseUrl: string,
  patch: Partial<Settings>,
): Promise<{ ok: true } | { ok: false; reason: string }> {
  const res = await deviceFetch(baseUrl, "/api/settings", { method: "POST", body: JSON.stringify(patch) });
  if (res.ok) return { ok: true };
  let reason = `Your Irin refused the change (${res.status}).`;
  try {
    const b = await res.json();
    if (b && typeof b.detail === "string") reason = `Your Irin refused the change: ${b.detail}`;
  } catch {
    /* no body */
  }
  return { ok: false, reason };
}

/** Key-order-insensitive JSON, so the Pi's echo of a nested object (a family
 * recipient, the LED colours) compares equal to what was sent. */
function stable(v: unknown): string {
  if (Array.isArray(v)) return `[${v.map(stable).join(",")}]`;
  if (v && typeof v === "object")
    return `{${Object.keys(v as object)
      .sort()
      .map((k) => `${JSON.stringify(k)}:${stable((v as Record<string, unknown>)[k] ?? null)}`)
      .join(",")}}`;
  return JSON.stringify(v ?? null);
}
export const sameValue = (a: unknown, b: unknown) => stable(a) === stable(b);
const same = sameValue;

/** The fields of `draft` that differ from the Pi's `current` settings. */
export function changedFields(current: Settings, draft: Settings): Partial<Settings> {
  const out: Record<string, unknown> = {};
  for (const k of Object.keys(draft) as (keyof Settings)[]) {
    if (!same(draft[k], current[k])) out[k] = draft[k];
  }
  return out as Partial<Settings>;
}

/** True once the Pi's settings carry every field of `patch`. */
export function applied(current: Settings, patch: Partial<Settings>): boolean {
  return (Object.keys(patch) as (keyof Settings)[]).every((k) => same(current[k], patch[k]));
}

function rgb(hex: string): [number, number, number] | null {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/** Euclidean RGB distance; the warning and full tiers must stay visually distinct. */
export function colorDistance(a: string, b: string): number {
  const x = rgb(a);
  const y = rgb(b);
  if (!x || !y) return 0;
  return Math.hypot(x[0] - y[0], x[1] - y[1], x[2] - y[2]);
}
export const MIN_TIER_DISTANCE = 120;

const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;

/** Problems that block saving; empty when the form is valid. */
export function validate(s: Settings): string[] {
  const e: string[] = [];
  // the same bounds the Pi enforces (SETTINGS_BOUNDS in backend/app/main.py)
  const low = s.low_threshold ?? 70;
  const high = s.high_threshold ?? 250;
  if (!(low >= 54 && low <= 100)) e.push("Low alarm must be 54–100 mg/dL.");
  else if (!(high >= 120 && high <= 400)) e.push("High alarm must be 120–400 mg/dL.");
  else if (!(low < high)) e.push("Low must be below high.");
  const lead = s.predictive_lead_min ?? 30;
  if (s.predictive_enabled && !(lead >= 5 && lead <= 60)) e.push("Prediction lead time must be 5–60 minutes.");
  if (!HHMM.test(s.night_window_start ?? "") || !HHMM.test(s.night_window_end ?? ""))
    e.push("Night window times must be HH:MM.");
  if (s.basal_time && !HHMM.test(s.basal_time)) e.push("Basal time must be HH:MM.");
  if (s.high_alert_mode === "remind" && !((s.high_remind_hours ?? 0) >= 0.5 && (s.high_remind_hours ?? 0) <= 24))
    e.push("Still-high reminder must be 0.5–24 hours.");
  const c = s.led_colors ?? {};
  if (colorDistance(c.warning ?? "", c.full ?? "") < MIN_TIER_DISTANCE)
    e.push("Warning and full-alarm light colours are too similar; they must stay easy to tell apart.");
  if (s.report_email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(s.report_email)) e.push("Email looks wrong.");
  return e;
}
