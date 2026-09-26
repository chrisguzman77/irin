import type { Settings } from "./contracts";

// The alarm sounds shipped in backend/sounds/ (buddy_chime is not an alarm).
export const ALARM_SOUNDS = ["alarm_soft", "alarm_urgent", "chirp"] as const;

/** The one place settings are saved. The backend has no settings endpoint
 * yet (chris.md step 8 lists it behind the PIN gate); wire it here through
 * deviceFetch when it lands. "Applied" is shown only when the Pi's
 * settings_change / snapshot carries the new values, never on the tap. */
export async function saveSettings(_baseUrl: string, _s: Settings): Promise<{ ok: boolean; reason?: string }> {
  return { ok: false, reason: "Not connected yet: your Irin has no settings endpoint." };
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
  const low = s.low_threshold ?? 70;
  const high = s.high_threshold ?? 250;
  if (!(low >= 40 && low < high && high <= 400)) e.push("Low must be below high (40–400 mg/dL).");
  const lead = s.predictive_lead_min ?? 30;
  if (s.predictive_enabled && !(lead >= 5 && lead <= 60)) e.push("Prediction lead time must be 5–60 minutes.");
  if (!HHMM.test(s.night_window_start ?? "") || !HHMM.test(s.night_window_end ?? ""))
    e.push("Night window times must be HH:MM.");
  if (s.basal_time && !HHMM.test(s.basal_time)) e.push("Basal time must be HH:MM.");
  if (s.high_alert_mode === "remind" && !((s.high_remind_hours ?? 0) > 0))
    e.push("Set how many hours before a still-high reminder.");
  const c = s.led_colors ?? {};
  if (colorDistance(c.warning ?? "", c.full ?? "") < MIN_TIER_DISTANCE)
    e.push("Warning and full-alarm light colours are too similar; they must stay easy to tell apart.");
  if (s.report_email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(s.report_email)) e.push("Email looks wrong.");
  return e;
}
