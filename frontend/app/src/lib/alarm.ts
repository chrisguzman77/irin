import type { components } from "../types/pi";
import { deviceFetch, PinRejected } from "./api";
import type { AlarmState } from "./contracts";

// Tier and intensity from the Pi's AlarmState (chris.md alarm state machine):
// pending = the predicted-low WARNING; active/rearmed = sounding; the tier
// comes from trigger_type, never from the number on screen.
export type LowTier = "warning" | "full";

export function soundingLow(a: AlarmState | undefined): LowTier | null {
  if (!a || !["pending", "active", "rearmed"].includes(a.state)) return null;
  if (a.trigger_type === "actual_low") return "full";
  if (a.trigger_type === "predicted_low") return "warning";
  return null;
}

export const isHigh = (a: AlarmState | undefined) => !!a && a.trigger_type === "high" && a.state !== "idle";
export const isAckedLow = (a: AlarmState | undefined) =>
  !!a && a.state === "acknowledged" && (a.trigger_type === "actual_low" || a.trigger_type === "predicted_low");

// U+FE0E forces the text glyph: some platforms draw ↗ ↘ as colour emoji.
export const TREND_ARROWS: Record<string, string> = Object.fromEntries(
  Object.entries({
    DoubleUp: "⇈", SingleUp: "↑", FortyFiveUp: "↗", Flat: "→",
    FortyFiveDown: "↘", SingleDown: "↓", DoubleDown: "⇊",
  }).map(([k, v]) => [k, v + "\uFE0E"]),
);

type AckRequest = components["schemas"]["AckRequest"];

/** The one place the phone's acknowledge is sent: POST /api/acknowledge with
 * source app and the session PIN (a 401 sends the user back to the gate).
 * Silence comes from the alarm_state_change that follows, never from local state. */
export async function sendAcknowledge(baseUrl: string): Promise<{ ok: boolean; reason?: string }> {
  const body: AckRequest = { source: "app" };
  try {
    const res = await deviceFetch(baseUrl, "/api/acknowledge", { method: "POST", body: JSON.stringify(body) });
    return res.ok ? { ok: true } : { ok: false, reason: `Your Irin refused (${res.status}).` };
  } catch (e) {
    if (e instanceof PinRejected) return { ok: false, reason: "Code not accepted." };
    return { ok: false, reason: "Could not reach your Irin." };
  }
}
