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

/** The one place the phone's acknowledge is sent (ack_source = app). The
 * backend has no acknowledge endpoint or WS command shape yet (chris.md
 * step 8); wire it here through deviceFetch when it lands. Silence comes
 * from the alarm_state_change that follows, never from local state. */
export async function sendAcknowledge(_baseUrl: string): Promise<{ ok: boolean; reason?: string }> {
  return { ok: false, reason: "Not connected yet: your Irin has no acknowledge endpoint." };
}
