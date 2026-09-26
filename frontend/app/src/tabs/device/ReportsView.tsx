import { useEffect, useState } from "react";
import { graphUrl, listReports, PARTIAL_COVERAGE_PCT, statsOf, type MorningReport } from "../../lib/reports";

// Step 7: stored morning reports by date, newest first. A replayed night is
// badged DEMO wherever it appears (invariant 1).

// one decimal where there is one, so a tile always matches the narrative's number
const pct = (v: number | null | undefined) => (v == null ? "—" : `${Number(v.toFixed(1))}%`);
const val = (v: number | null | undefined) => (v == null ? "—" : String(Math.round(v)));

const dateLabel = (d: string) =>
  // night_date is a plain date: parse it as local noon so no timezone shifts the day
  new Date(`${d}T12:00:00`).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });

function DemoBadge() {
  return <span className="bg-amber-400 text-black text-xs font-bold px-2 py-0.5 rounded">DEMO</span>;
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="bg-neutral-900 rounded-lg px-3 py-2">
      <div className="text-xs uppercase tracking-wide text-neutral-400">{label}</div>
      <div className="text-2xl font-bold tabular-nums">
        {value}
        {sub && value !== "—" && <span className="text-sm font-normal text-neutral-400 ml-1">{sub}</span>}
      </div>
    </div>
  );
}

function ReportDetail({ report, baseUrl, onBack }: { report: MorningReport; baseUrl: string; onBack: () => void }) {
  const s = statsOf(report);
  const img = graphUrl(baseUrl, report);
  const [imgFailed, setImgFailed] = useState(false);
  return (
    <section className="flex flex-col gap-3">
      <button type="button" onClick={onBack} className="self-start text-neutral-400 text-sm">
        ← All reports
      </button>
      <div className="flex items-center gap-2">
        <h2 className="text-xl font-semibold">Night of {dateLabel(report.night_date)}</h2>
        {report.is_demo && <DemoBadge />}
      </div>
      {s.coverage_pct != null && s.coverage_pct < PARTIAL_COVERAGE_PCT && (
        <p className="border border-neutral-600 text-neutral-300 text-sm rounded-lg px-3 py-2">
          The sensor covered {pct(s.coverage_pct)} of the night; some of it is missing.
        </p>
      )}
      <div className="grid grid-cols-2 gap-2">
        <Stat label="In range" value={pct(s.tir_pct)} />
        <Stat label="Below 70" value={pct(s.tbr_pct)} sub={s.minutes_below_70 ? `${s.minutes_below_70} min` : undefined} />
        <Stat label="Low" value={val(s.low_mgdl)} sub={s.low_at ? `at ${s.low_at}` : undefined} />
        <Stat label="High" value={val(s.high_mgdl)} sub={s.high_at ? `at ${s.high_at}` : undefined} />
        <Stat label="Above 180" value={pct(s.tar_pct)} />
        <Stat label="Sensor coverage" value={pct(s.coverage_pct)} />
      </div>
      {img && !imgFailed && (
        <img
          src={img}
          alt={`Glucose overnight, ${report.night_date}`}
          className="w-full rounded-lg bg-white"
          onError={() => setImgFailed(true)}
        />
      )}
      <div className="whitespace-pre-line text-neutral-200 leading-relaxed">{report.narrative}</div>
      {report.audio_url && <audio controls src={report.audio_url} className="w-full" />}
    </section>
  );
}

export default function ReportsView({ baseUrl, mode }: { baseUrl: string; mode: string | undefined }) {
  const [reports, setReports] = useState<MorningReport[] | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<string | null>(null);

  // refetch on a live/demo switch so the list never lags the source
  useEffect(() => {
    let cancelled = false;
    listReports(baseUrl)
      .then((r) => !cancelled && (setReports(r), setError("")))
      .catch(() => !cancelled && setError("Could not load reports from your Irin."));
    return () => {
      cancelled = true;
    };
  }, [baseUrl, mode]);

  const current = reports?.find((r) => r.report_id === open);
  if (current) return <ReportDetail report={current} baseUrl={baseUrl} onBack={() => setOpen(null)} />;

  if (error) return <p className="text-red-400">{error}</p>;
  if (!reports) return <p className="text-neutral-400">Loading reports…</p>;
  if (reports.length === 0)
    return <p className="text-neutral-400">No morning reports yet. Your Irin writes one when the night window ends.</p>;

  return (
    <ul className="flex flex-col gap-2">
      {reports.map((r) => {
        const s = statsOf(r);
        return (
          <li key={r.report_id}>
            <button
              type="button"
              onClick={() => setOpen(r.report_id)}
              className="w-full flex items-center gap-3 bg-neutral-900 rounded-lg px-4 py-3 text-left"
            >
              <span className="flex-1 font-semibold">{dateLabel(r.night_date)}</span>
              {r.is_demo && <DemoBadge />}
              <span className="text-sm text-neutral-400 tabular-nums">
                {pct(s.tir_pct)} in range · low {val(s.low_mgdl)}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
