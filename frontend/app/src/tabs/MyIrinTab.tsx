import { useEffect, useState } from "react";
import { DASHBOARDS, fetchDash, type DashName, type DashResult } from "../lib/dash";

// My Irin (justin.md A4): eleven dashboards over Irin Cloud, one fetch each.
// Until dash.py (C3) pins each response's fields, a panel says what the cloud
// answered and draws nothing it would have to guess. The owner bearer arrives
// with A2 pairing; until then the requests go without it.
const RANGES = [14, 30, 90] as const;

function Panel({ title, about, result }: { title: string; about: string; result: DashResult | undefined }) {
  let line: string;
  let tone = "text-neutral-400";
  if (!result) line = "Loading…";
  else if (result.state === "not_yet") line = `Not served yet (${result.detail}).`;
  else if (result.state === "no_access") line = "Needs this phone paired as the owner (arrives with pairing).";
  else if (result.state === "error") {
    line = `${result.detail}.`;
    tone = "text-amber-300";
  } else {
    line = "Data arrived; the chart is drawn once its fields are pinned in cloud/README.md.";
    tone = "text-emerald-400";
  }
  return (
    <li className="rounded-xl border border-neutral-800 bg-neutral-950 p-4 flex flex-col gap-1">
      <h3 className="font-semibold">{title}</h3>
      <p className="text-sm text-neutral-500">{about}</p>
      <p className={`text-sm ${tone}`}>{line}</p>
    </li>
  );
}

export default function MyIrinTab() {
  const [days, setDays] = useState<(typeof RANGES)[number]>(14);
  const [results, setResults] = useState<Partial<Record<DashName, DashResult>>>({});

  useEffect(() => {
    let alive = true;
    setResults({});
    for (const d of DASHBOARDS)
      fetchDash(d.name, days, null).then((r) => alive && setResults((prev) => ({ ...prev, [d.name]: r })));
    return () => {
      alive = false;
    };
  }, [days]);

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
        {DASHBOARDS.map((d) => (
          <Panel key={d.name} title={d.title} about={d.about} result={results[d.name]} />
        ))}
      </ul>
    </section>
  );
}
