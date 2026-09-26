import type { StateSnapshot } from "../../lib/contracts";
import { isAckedLow, isHigh, TREND_ARROWS } from "../../lib/alarm";
import FamilyStories from "./FamilyStories";

const hhmm = (iso: string) =>
  new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });

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
  if (!snap) return <p className="text-neutral-400">Waiting for your Irin…</p>;
  const r = snap.latest_reading;
  const readingStale = !r || r.is_stale || stale;
  const fc = snap.forecast && r && !readingStale ? snap.forecast : null; // never forecast on stale
  return (
    <>
      <section className="flex flex-col items-center gap-1 py-6">
        {r ? (
          <>
            <div className="flex items-center gap-3">
              <span
                className={`text-8xl font-bold tabular-nums ${readingStale ? "text-neutral-500" : "text-white"} ${
                  r.is_stale ? "line-through" : ""
                }`}
              >
                {Math.round(r.glucose_mgdl)}
              </span>
              {!readingStale && <span className="text-5xl text-neutral-300">{TREND_ARROWS[r.trend] ?? "?"}</span>}
            </div>
            <div className="text-neutral-400">mg/dL · reading at {hhmm(r.timestamp)}</div>
            {r.is_stale && (
              <div className="mt-2 bg-neutral-500 text-black font-bold px-3 py-1 rounded">STALE — no new reading</div>
            )}
          </>
        ) : (
          <div className="text-neutral-400">No reading yet</div>
        )}
        {fc && (
          <div className={`mt-3 ${fc.predicted_mgdl < 70 ? "text-red-400" : "text-neutral-300"}`}>
            in {fc.horizon_min} min: about {Math.round(fc.predicted_mgdl)}
          </div>
        )}
        <div className="flex gap-2 mt-3">
          {isHigh(snap.alarm) && <span className="bg-amber-400 text-black text-sm font-bold px-2 py-1 rounded">HIGH</span>}
          {isAckedLow(snap.alarm) && (
            <span className="border border-red-500 text-red-300 text-sm font-bold px-2 py-1 rounded">
              acknowledged — still low
            </span>
          )}
        </div>
      </section>
      {basalNudge && (
        <div className="flex items-center gap-3 rounded-lg border border-sky-700 bg-sky-950 px-4 py-3 text-sky-100">
          <span className="flex-1 text-sm">
            Basal not logged yet{snap.settings?.basal_time ? ` (usual time ${snap.settings.basal_time})` : ""}.
          </span>
          {onLog && (
            <button type="button" onClick={onLog} className="rounded-lg bg-sky-200 text-black px-3 py-1.5 text-sm font-semibold">
              Log it
            </button>
          )}
        </div>
      )}
      {baseUrl && <FamilyStories snap={snap} baseUrl={baseUrl} />}
    </>
  );
}
