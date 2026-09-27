import { useEffect, useState } from "react";
import { useDevice } from "../lib/device";
import {
  DASHBOARDS, fetchDash, ownerBearer,
  type AlarmRow, type BasalRow, type BuddyRow, type StepNightRow, type StepWatchBody, type DashName, type DashResult, type HeatRow, type NearMissRow, type NightRow,
  type ProfileRow, type SensorRow, type TirRow, type UnderTheHood,
} from "../lib/dash";
import {
  AlarmsChart, BasalChart, BuddyChart, HeatChart, StepWatchChart, NearMissChart, NightsChart, ProfileChart, SensorChart, TirChart, UnderTheHoodView, md,
} from "./myirin/Charts";

// My Irin (justin.md A4 / C4): eleven dashboards over Irin Cloud, one fetch
// and one drawing each, from the fields cloud/README.md pins. While the paired
// Irin plays a demo, the panels read the demo device (demo=true) and say DEMO;
// real and replayed data are never drawn as each other.
const RANGES = [14, 30, 90] as const;

function Body({ name, result }: { name: DashName; result: DashResult | undefined }) {
  if (!result) return <p className="text-sm text-neutral-400">Loading…</p>;
  if (result.state === "not_yet") return <p className="text-sm text-neutral-400">Not served yet ({result.detail}).</p>;
  if (result.state === "no_access")
    return <p className="text-sm text-neutral-400">Needs the dashboard token: type it once under Device → Settings.</p>;
  if (result.state === "error") return <p className="text-sm text-amber-300">{result.detail}.</p>;
  const b = result.body;
  if (!b.available) return <p className="text-sm text-neutral-400">Not available yet{b.reason ? `: ${b.reason}` : "."}</p>;
  if (b.empty)
    return <p className="text-sm text-neutral-400">{name === "step_watch" ? "No Step Watch in this range." : "No data in this range."}</p>;
  const rows = (b.rows ?? []) as never[];
  if (name !== "under_the_hood" && rows.length === 0) return <p className="text-sm text-neutral-400">No data in this range.</p>;
  switch (name) {
    case "nights": return <NightsChart rows={rows as NightRow[]} />;
    case "tir": return <TirChart rows={rows as TirRow[]} />;
    case "profile": return <ProfileChart rows={rows as ProfileRow[]} />;
    case "lows_heatmap": return <HeatChart rows={rows as HeatRow[]} />;
    case "alarms": return <AlarmsChart rows={rows as AlarmRow[]} />;
    case "near_misses": return <NearMissChart rows={rows as NearMissRow[]} />;
    case "basal": return <BasalChart rows={rows as BasalRow[]} />;
    case "sensor": return <SensorChart rows={rows as SensorRow[]} />;
    case "step_watch": return <StepWatchChart body={b as unknown as StepWatchBody} rows={rows as StepNightRow[]} />;
    case "buddy": return <BuddyChart rows={rows as BuddyRow[]} />;
    case "under_the_hood": return <UnderTheHoodView u={b as unknown as UnderTheHood} />;
    default: return <p className="text-sm text-neutral-400">Not drawn yet.</p>;
  }
}

export default function MyIrinTab() {
  const { socket } = useDevice();
  const demo = socket.snapshot?.mode === "replay";
  const [days, setDays] = useState<(typeof RANGES)[number]>(14);
  const [results, setResults] = useState<Partial<Record<DashName, DashResult>>>({});

  useEffect(() => {
    let alive = true;
    setResults({});
    for (const d of DASHBOARDS)
      fetchDash(d.name, days, demo, ownerBearer()).then((r) => alive && setResults((prev) => ({ ...prev, [d.name]: r })));
    return () => {
      alive = false;
    };
  }, [days, demo]);

  return (
    <section className="flex flex-col gap-4">
      <h2 className="text-xl font-semibold">My Irin</h2>
      <p className="text-sm text-neutral-400">Pictures of your nights from Irin Cloud. They never decide anything: alarms and cards run on your Irin.</p>
      <div className="flex gap-2" role="group" aria-label="range">
        {RANGES.map((r) => (
          <button
            key={r}
            type="button"
            aria-pressed={days === r}
            onClick={() => setDays(r)}
            className={`px-3 py-1.5 rounded-full text-sm ${days === r ? "bg-white text-black font-semibold" : "bg-neutral-900 text-neutral-300"}`}
          >
            {r} days
          </button>
        ))}
      </div>
      <ul className="flex flex-col gap-3">
        {DASHBOARDS.map((d) => {
          const r = results[d.name];
          const body = r?.state === "ok" ? r.body : null;
          return (
            <li key={d.name} className="rounded-xl border border-neutral-800 bg-neutral-950 p-4 flex flex-col gap-2" aria-label={d.title}>
              <div className="flex items-center gap-2">
                <h3 className="font-semibold flex-1">{d.title}</h3>
                {body?.is_demo && <span className="bg-amber-400 text-black text-xs font-bold px-2 py-0.5 rounded">DEMO</span>}
              </div>
              <p className="text-xs text-neutral-500">
                {d.about}
                {body?.as_of ? ` · up to ${md(body.as_of)} ${body.as_of.slice(11, 16)}` : ""}
              </p>
              <Body name={d.name} result={r} />
            </li>
          );
        })}
      </ul>
    </section>
  );
}
