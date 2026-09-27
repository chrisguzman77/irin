import type { StateSnapshot } from "../../lib/contracts";
import { isAckedLow, isHigh, TREND_ARROWS } from "../../lib/alarm";
import FamilyStories from "./FamilyStories";

// 12-hour with AM/PM, like the kiosk
const hhmm = (iso: string) =>
  new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", hour12: true });

// Step 4: the live view, rendered only from the snapshot (+ updates).
export default function LiveView({
  snap,
  stale,
  basalNudge = false,
  onLog,
  baseUrl,
}: {
  snap: StateSnapshot | null;
  stale: boolean;
  basalNudge?: boolean;
  onLog?: () => void;
  baseUrl?: string;
}) {
  if (!snap) return <p className="text-irin-sage">Waiting for your Irin…</p>;
  const r = snap.latest_reading;
  const readingStale = !r || r.is_stale || stale;
  const fc = snap.forecast && r && !readingStale ? snap.forecast : null; // never forecast on stale
  return (
    <>
      <section className="flex flex-col items-center gap-1 rounded-3xl border border-irin-leaf/50 bg-irin-surface px-5 pt-5 pb-6 shadow-lg shadow-black/30">
        <div className="text-xs font-semibold uppercase tracking-[0.2em] text-irin-sage">Your glucose right now</div>
        {r ? (
          <>
            <div className="flex items-center gap-3">
              <span
                className={`text-8xl font-bold tabular-nums ${readingStale ? "text-neutral-500" : "text-irin-cream"} ${
                  r.is_stale ? "line-through" : ""
                }`}
              >
                {Math.round(r.glucose_mgdl)}
              </span>
              {!readingStale && <span className="text-5xl text-irin-mint">{TREND_ARROWS[r.trend] ?? "?"}</span>}
            </div>
            <div className="text-irin-sage">mg/dL · last reading {hhmm(r.timestamp)}</div>
            {r.is_stale && (
              <div className="mt-2 bg-neutral-500 text-black font-bold px-3 py-1 rounded">STALE — no new reading</div>
            )}
          </>
        ) : (
          <div className="py-6 text-irin-sage">No reading yet</div>
        )}
        {fc && (
          <div
            className={`mt-4 rounded-full px-4 py-1.5 text-sm ${
              fc.predicted_mgdl < 70 ? "bg-red-950 text-red-300 font-semibold" : "bg-irin-ink text-irin-mint"
            }`}
          >
            In {fc.horizon_min} min, about {Math.round(fc.predicted_mgdl)}
          </div>
        )}
        <div className="flex gap-2 mt-3 empty:hidden">
          {isHigh(snap.alarm) && <span className="bg-amber-400 text-black text-sm font-bold px-2 py-1 rounded">HIGH</span>}
          {isAckedLow(snap.alarm) && (
            <span className="border border-red-500 text-red-300 text-sm font-bold px-2 py-1 rounded">
              acknowledged — still low
            </span>
          )}
        </div>
      </section>
      {basalNudge && (
        <div className="mt-4 flex items-center gap-3 rounded-2xl border border-irin-leaf/60 bg-irin-surface px-4 py-3 text-irin-cream">
          <span className="flex-1 text-sm">
            Basal not logged yet{snap.settings?.basal_time ? ` (usual time ${snap.settings.basal_time})` : ""}.
          </span>
          {onLog && (
            <button type="button" onClick={onLog} className="rounded-full bg-irin-mint text-irin-ink px-4 py-1.5 text-sm font-semibold">
              Log it
            </button>
          )}
        </div>
      )}
      {baseUrl && <FamilyStories snap={snap} baseUrl={baseUrl} />}
    </>
  );
}
