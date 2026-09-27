import { useState } from "react";
import type {
  AlarmRow, BasalRow, BuddyRow, HeatRow, NearMissRow, NightRow, ProfileRow, SensorRow, StepNightRow, StepWatchBody, TirRow,
  UnderTheHood,
} from "../../lib/dash";

// One small SVG drawing per dashboard, from the fields cloud/README.md pins and
// nothing else. Pictures only (invariant 21): no chart decides or labels a
// night as anything the data does not say. Timestamps are the Pi's naive local
// time and are read as text, never through a timezone.

const W = 320;
const M = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export const md = (iso: string | null | undefined) => {
  if (!iso) return "—";
  const [, m, d] = iso.slice(0, 10).split("-").map(Number);
  return m ? `${M[m - 1]} ${d}` : "—";
};
const pct = (v: number) => `${Math.round(v * 100)}%`;
const num1 = (v: number) => String(Number(v.toFixed(1)));

function Axis({ y, label, x0 = 28, x1 = W - 4, dash = false, color = "#404040" }: { y: number; label?: string; x0?: number; x1?: number; dash?: boolean; color?: string }) {
  return (
    <g>
      <line x1={x0} x2={x1} y1={y} y2={y} stroke={color} strokeWidth="1" strokeDasharray={dash ? "4 3" : undefined} />
      {label && <text x={x0 - 4} y={y + 3} textAnchor="end" fontSize="9" fill="#a3a3a3">{label}</text>}
    </g>
  );
}
function Ends({ first, last, y }: { first: string; last: string; y: number }) {
  return (
    <g fontSize="9" fill="#a3a3a3">
      <text x={28} y={y}>{first}</text>
      <text x={W - 4} y={y} textAnchor="end">{last}</text>
    </g>
  );
}

// ------------------------------------------------------------ nights: one tile per night

export function NightsChart({ rows }: { rows: NightRow[] }) {
  const [sel, setSel] = useState<string | null>(null);
  const picked = rows.find((r) => r.night_date === sel);
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap gap-1">
        {rows.map((r) => {
          const thin = r.coverage_pct < 85;
          const low = r.minutes_below_70 > 0;
          return (
            <button
              key={r.night_date}
              type="button"
              onClick={() => setSel(sel === r.night_date ? null : r.night_date)}
              aria-pressed={sel === r.night_date}
              title={md(r.night_date)}
              className={`w-9 h-11 rounded text-[10px] leading-tight flex flex-col items-center justify-center ${
                thin ? "border border-dashed border-neutral-600 text-neutral-500" : low ? "bg-red-900/70 text-red-100" : "bg-emerald-900/60 text-emerald-100"
              } ${sel === r.night_date ? "ring-2 ring-white" : ""}`}
            >
              <span>{r.night_date.slice(8, 10)}</span>
              <span className="font-semibold">{r.low_point_mgdl === null ? "—" : Math.round(r.low_point_mgdl)}</span>
            </button>
          );
        })}
      </div>
      <p className="text-xs text-neutral-500">Number = the night&apos;s low point. Red: time under 70. Dashed: under 85% sensor coverage (too thin to read).</p>
      {picked && (
        <p className="text-sm">
          Night of {md(picked.night_date)}: low point {picked.low_point_mgdl === null ? "—" : Math.round(picked.low_point_mgdl)}, {picked.minutes_below_70} min under 70,
          coverage {num1(picked.coverage_pct)}% ({picked.readings} readings).
        </p>
      )}
    </div>
  );
}

// ------------------------------------------------------------ tir: stacked daily bars + 7/30-day lines

export function TirChart({ rows }: { rows: TirRow[] }) {
  const H = 140, top = 6, bot = 118, x0 = 28;
  const bw = (W - x0 - 4) / rows.length;
  const y = (v: number) => bot - v * (bot - top);
  const line = (key: "in_range_7d" | "in_range_30d") =>
    rows.map((r, i) => (r[key] === null ? null : `${x0 + bw * (i + 0.5)},${y(r[key] as number)}`)).filter(Boolean).join(" ");
  const last = rows[rows.length - 1];
  return (
    <div className="flex flex-col gap-1">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="time in range by day">
        <Axis y={y(1)} label="100%" />
        <Axis y={y(0.5)} label="50%" />
        <Axis y={bot} label="0" />
        {rows.map((r, i) => {
          const x = x0 + bw * i + bw * 0.1, w = bw * 0.8;
          const u = r.share_under_70, n = r.share_70_180, o = r.share_over_180;
          return (
            <g key={r.day}>
              <rect x={x} y={y(u)} width={w} height={bot - y(u)} fill="#b91c1c" />
              <rect x={x} y={y(u + n)} width={w} height={y(u) - y(u + n)} fill="#15803d" />
              <rect x={x} y={y(u + n + o)} width={w} height={y(u + n) - y(u + n + o)} fill="#b45309" />
            </g>
          );
        })}
        <polyline points={line("in_range_30d")} fill="none" stroke="#e5e5e5" strokeWidth="1.5" strokeDasharray="4 3" />
        <polyline points={line("in_range_7d")} fill="none" stroke="#ffffff" strokeWidth="2" />
        <Ends first={md(rows[0].day)} last={md(last.day)} y={H - 6} />
      </svg>
      <p className="text-xs text-neutral-500">
        Red under 70 · green 70–180 · amber over 180 (share of the readings present). Lines: 7-day (solid) and 30-day (dashed) in range.
        {last.in_range_7d !== null && ` Last 7 days: ${pct(last.in_range_7d)} in range.`}
      </p>
    </div>
  );
}

// ------------------------------------------------------------ profile: median + 10-90 band by time of day

const minutes = (t: string) => {
  const [h, m] = t.split(":").map(Number);
  return h * 60 + m;
};
export function ProfileChart({ rows }: { rows: ProfileRow[] }) {
  const H = 150, top = 6, bot = 128, x0 = 28;
  const lo = Math.min(40, ...rows.map((r) => r.p10)), hi = Math.max(250, ...rows.map((r) => r.p90));
  const x = (t: string) => x0 + (minutes(t) / 1440) * (W - x0 - 4);
  const y = (v: number) => bot - ((v - lo) / (hi - lo)) * (bot - top);
  const sorted = [...rows].sort((a, b) => minutes(a.time_of_day) - minutes(b.time_of_day));
  const band = [...sorted.map((r) => `${x(r.time_of_day)},${y(r.p90)}`), ...sorted.reverse().map((r) => `${x(r.time_of_day)},${y(r.p10)}`)].join(" ");
  sorted.reverse();
  return (
    <div className="flex flex-col gap-1">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="glucose by time of day">
        <rect x={x0} y={y(180)} width={W - x0 - 4} height={y(70) - y(180)} fill="#14532d" opacity="0.35" />
        <Axis y={y(180)} label="180" />
        <Axis y={y(70)} label="70" />
        <polygon points={band} fill="#a3a3a3" opacity="0.3" />
        <polyline points={sorted.map((r) => `${x(r.time_of_day)},${y(r.p50)}`).join(" ")} fill="none" stroke="#fff" strokeWidth="2" />
        <g fontSize="9" fill="#a3a3a3">
          {[0, 6, 12, 18, 24].map((h) => (
            <text key={h} x={x0 + (h / 24) * (W - x0 - 4)} y={H - 6} textAnchor={h === 0 ? "start" : h === 24 ? "end" : "middle"}>{String(h % 24).padStart(2, "0")}:00</text>
          ))}
        </g>
      </svg>
      <p className="text-xs text-neutral-500">White line: the median. Grey band: 10th to 90th percentile. Green: 70–180.</p>
    </div>
  );
}

// ------------------------------------------------------------ lows heatmap: weekday x hour

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
export function HeatChart({ rows }: { rows: HeatRow[] }) {
  const x0 = 28, cw = (W - x0 - 4) / 24, ch = 14, H = 7 * ch + 18;
  const max = Math.max(0.01, ...rows.map((r) => r.share_under_70));
  const at = new Map(rows.map((r) => [`${r.weekday}:${r.hour_of_day}`, r]));
  return (
    <div className="flex flex-col gap-1">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="lows by weekday and hour">
        {DAYS.map((d, i) => (
          <g key={d}>
            <text x={x0 - 4} y={i * ch + 10} textAnchor="end" fontSize="9" fill="#a3a3a3">{d}</text>
            {Array.from({ length: 24 }, (_, h) => {
              const r = at.get(`${i + 1}:${h}`);
              return (
                <rect key={h} x={x0 + h * cw + 0.5} y={i * ch + 0.5} width={cw - 1} height={ch - 1}
                  fill={r && r.readings > 0 ? "#dc2626" : "none"} opacity={r && r.readings > 0 ? 0.08 + 0.92 * (r.share_under_70 / max) : 1}
                  stroke={r && r.readings > 0 ? "none" : "#262626"}>
                  <title>{`${d} ${String(h).padStart(2, "0")}:00: ${r ? `${r.under_70} of ${r.readings} readings under 70` : "no readings"}`}</title>
                </rect>
              );
            })}
          </g>
        ))}
        <g fontSize="9" fill="#a3a3a3">
          {[0, 6, 12, 18].map((h) => <text key={h} x={x0 + h * cw} y={H - 4}>{String(h).padStart(2, "0")}</text>)}
        </g>
      </svg>
      <p className="text-xs text-neutral-500">Darker red: a larger share of that hour&apos;s readings under 70. Outlined: no readings.</p>
    </div>
  );
}

// ------------------------------------------------------------ weekly bars (alarms by tier, near-misses)

// AlarmEvent.tier (contracts): predicted_low | actual_low | stale | high
const TIER_COLOR: Record<string, string> = { predicted_low: "#d97706", actual_low: "#dc2626", stale: "#737373", high: "#a855f7" };
function WeekBars({ weeks, stacks, legend }: { weeks: string[]; stacks: { key: string; color: string; values: number[] }[]; legend: string }) {
  const H = 130, top = 6, bot = 108, x0 = 28;
  const totals = weeks.map((_, i) => stacks.reduce((s, k) => s + k.values[i], 0));
  const max = Math.max(1, ...totals);
  const bw = (W - x0 - 4) / weeks.length;
  const y = (v: number) => bot - (v / max) * (bot - top);
  return (
    <div className="flex flex-col gap-1">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label={legend}>
        <Axis y={y(max)} label={String(max)} />
        <Axis y={bot} label="0" />
        {weeks.map((w, i) => {
          let acc = 0;
          return (
            <g key={w}>
              {stacks.map((s) => {
                const v = s.values[i];
                const r = <rect key={s.key} x={x0 + bw * i + bw * 0.15} y={y(acc + v)} width={bw * 0.7} height={y(acc) - y(acc + v)} fill={s.color} />;
                acc += v;
                return r;
              })}
            </g>
          );
        })}
        <Ends first={`wk of ${md(weeks[0])}`} last={`wk of ${md(weeks[weeks.length - 1])}`} y={H - 6} />
      </svg>
      <p className="text-xs text-neutral-500">{legend}</p>
    </div>
  );
}

export function AlarmsChart({ rows }: { rows: AlarmRow[] }) {
  const weeks = [...new Set(rows.map((r) => r.week))].sort();
  const tiers = [...new Set(rows.map((r) => r.tier))].sort();
  const stacks = tiers.map((t, i) => ({
    key: t,
    color: TIER_COLOR[t] ?? ["#a3a3a3", "#737373", "#525252"][i % 3],
    values: weeks.map((w) => rows.find((r) => r.week === w && r.tier === t)?.alarms ?? 0),
  }));
  const total = rows.reduce((s, r) => s + r.alarms, 0);
  const esc = rows.reduce((s, r) => s + r.escalated, 0);
  const rearms = rows.reduce((s, r) => s + r.rearms, 0);
  const acks = rows.filter((r) => r.mean_ack_min !== null);
  return (
    <div className="flex flex-col gap-1">
      <WeekBars weeks={weeks} stacks={stacks} legend={`Alarms per week: ${tiers.map((t) => t.replace(/_/g, " ")).join(", ")}.`} />
      <p className="text-sm">
        {total} alarm{total === 1 ? "" : "s"}, {esc} escalated, {rearms} re-armed
        {acks.length ? `; acknowledged in ${num1(acks.reduce((s, r) => s + (r.mean_ack_min as number), 0) / acks.length)} min on average` : ""}.
      </p>
    </div>
  );
}

export function NearMissChart({ rows }: { rows: NearMissRow[] }) {
  const sorted = [...rows].sort((a, b) => a.week.localeCompare(b.week));
  return (
    <WeekBars
      weeks={sorted.map((r) => r.week)}
      stacks={[{ key: "n", color: "#d97706", values: sorted.map((r) => r.near_misses) }]}
      legend={`Near-misses per week: ${sorted.reduce((s, r) => s + r.near_misses, 0)} in all.`}
    />
  );
}

// ------------------------------------------------------------ basal: time of day of each dose

export function BasalChart({ rows }: { rows: BasalRow[] }) {
  const H = 140, top = 8, bot = 118, x0 = 34;
  const sorted = [...rows].sort((a, b) => a.time.localeCompare(b.time));
  const t0 = Date.parse(sorted[0].time.slice(0, 10)), t1 = Date.parse(sorted[sorted.length - 1].time.slice(0, 10));
  const span = Math.max(1, t1 - t0);
  const lo = Math.min(...sorted.map((r) => r.minutes_of_day)) - 60, hi = Math.max(...sorted.map((r) => r.minutes_of_day)) + 60;
  const x = (iso: string) => x0 + ((Date.parse(iso.slice(0, 10)) - t0) / span) * (W - x0 - 10);
  const y = (m: number) => top + ((m - lo) / Math.max(1, hi - lo)) * (bot - top);
  const hhmm = (m: number) => `${String(Math.floor(((m % 1440) + 1440) % 1440 / 60)).padStart(2, "0")}:${String(Math.round(((m % 60) + 60) % 60)).padStart(2, "0")}`;
  return (
    <div className="flex flex-col gap-1">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="basal dose times">
        <Axis y={y(lo + 60)} label={hhmm(lo + 60)} x0={x0} />
        <Axis y={y(hi - 60)} label={hhmm(hi - 60)} x0={x0} />
        {sorted.map((r) => (
          <circle key={r.time} cx={x(r.time)} cy={y(r.minutes_of_day)} r="3.5" fill={r.confirmed ? "#60a5fa" : "none"} stroke="#60a5fa" strokeWidth="1.5">
            <title>{`${md(r.time)} ${r.time.slice(11, 16)}: ${num1(r.insulin_units)} units${r.confirmed ? "" : " (not confirmed)"}`}</title>
          </circle>
        ))}
        <Ends first={md(sorted[0].time)} last={md(sorted[sorted.length - 1].time)} y={H - 6} />
      </svg>
      <p className="text-xs text-neutral-500">Each dot is a basal dose at its time of day. Hollow: not confirmed.</p>
    </div>
  );
}

// ------------------------------------------------------------ sensor: daily coverage

export function SensorChart({ rows }: { rows: SensorRow[] }) {
  const H = 130, top = 6, bot = 108, x0 = 28;
  const sorted = [...rows].sort((a, b) => a.day.localeCompare(b.day));
  const bw = (W - x0 - 4) / sorted.length;
  const y = (v: number) => bot - (v / 100) * (bot - top);
  const gaps = sorted.reduce((s, r) => s + r.gap_minutes, 0);
  return (
    <div className="flex flex-col gap-1">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="sensor coverage by day">
        <Axis y={y(100)} label="100%" />
        <Axis y={y(85)} label="85%" dash color="#737373" />
        <Axis y={bot} label="0" />
        {sorted.map((r, i) => (
          <rect key={r.day} x={x0 + bw * i + bw * 0.15} y={y(r.coverage_pct)} width={bw * 0.7} height={bot - y(r.coverage_pct)}
            fill={r.coverage_pct < 85 ? "#525252" : "#0ea5e9"}>
            <title>{`${md(r.day)}: ${num1(r.coverage_pct)}% (${r.gap_minutes} min of gaps)`}</title>
          </rect>
        ))}
        <Ends first={md(sorted[0].day)} last={md(sorted[sorted.length - 1].day)} y={H - 6} />
      </svg>
      <p className="text-xs text-neutral-500">Grey: under 85% (too thin for Irin to draw a conclusion from). {gaps} min of gaps in all.</p>
    </div>
  );
}

// ------------------------------------------------------------ under the hood

const bytes = (n: number | null) => (n === null ? "—" : n > 1e9 ? `${num1(n / 1e9)} GB` : n > 1e6 ? `${num1(n / 1e6)} MB` : n > 1e3 ? `${num1(n / 1e3)} kB` : `${n} B`);
const when = (iso: string | null) => (iso ? `${md(iso)} ${iso.slice(11, 16)}` : "—");
export function UnderTheHoodView({ u }: { u: UnderTheHood }) {
  const rows: [string, string][] = [
    ["Readings stored", u.readings.toLocaleString()],
    ["First reading", when(u.first_reading)],
    ["Last reading", when(u.last_reading)],
    ["Last sync from the Pi", when(u.last_sync)],
    ["Aggregates refreshed", when(u.last_aggregate_refresh)],
    ["Readings table (all devices)", bytes(u.readings_table_bytes_all_devices)],
    ["Compressed", `${bytes(u.compressed_before_bytes)} → ${bytes(u.compressed_after_bytes)}${u.compression_ratio !== null ? ` (${num1(u.compression_ratio)}×)` : ""}`],
  ];
  return (
    <dl className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-1 text-sm">
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-neutral-400">{k}</dt>
          <dd className="text-right tabular-nums">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

// ------------------------------------------------------------ step_watch: low point against the baseline band

const GI_COLOR: Record<string, string> = { fine: "#34d399", rough: "#f59e0b", cant_eat: "#ef4444" };
const GI_LABEL: Record<string, string> = { fine: "fine", rough: "rough", cant_eat: "can't eat" };

export function StepWatchChart({ body, rows }: { body: StepWatchBody; rows: StepNightRow[] }) {
  const H = 150, top = 8, bot = 112, x0 = 34;
  const sorted = [...rows].sort((a, b) => a.night_date.localeCompare(b.night_date));
  const base = body.baseline?.low_point_mgdl ?? null;
  const vals = sorted.map((r) => r.low_point_mgdl).filter((v): v is number => v !== null);
  const lo = Math.min(54, ...vals, base ?? 200) - 5, hi = Math.max(120, ...vals, base ?? 0) + 5;
  const n = sorted.length;
  const x = (i: number) => x0 + (n <= 1 ? (W - x0 - 10) / 2 : (i / (n - 1)) * (W - x0 - 10));
  const y = (v: number) => bot - ((v - lo) / (hi - lo)) * (bot - top);
  const idx = new Map(sorted.map((r, i) => [r.night_date, i]));
  const plan = body.plan;
  return (
    <div className="flex flex-col gap-2">
      {plan && (
        <p className="text-sm text-neutral-300">
          {plan.drug_label} · {plan.status} · since {md(plan.started_at)} ·{" "}
          {plan.steps.map((s) => `${s.dose_label} from ${md(s.planned_start)}`).join(", ")}
        </p>
      )}
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="nightly low point against baseline">
        <Axis y={y(70)} label="70" x0={x0} color="#7f1d1d" />
        {base !== null && <Axis y={y(base)} label={String(Math.round(base))} x0={x0} dash color="#a3a3a3" />}
        {sorted.map((r, i) =>
          r.low_point_mgdl === null ? null : (
            <circle key={r.night_date} cx={x(i)} cy={y(r.low_point_mgdl)} r="3.5"
              fill={r.coverage_pct < 85 ? "none" : "#e5e5e5"} stroke="#e5e5e5" strokeWidth="1.5">
              <title>{`${md(r.night_date)}: low ${Math.round(r.low_point_mgdl)}${
                r.vs_baseline_mgdl !== null ? ` (${r.vs_baseline_mgdl > 0 ? "+" : ""}${num1(r.vs_baseline_mgdl)} vs baseline)` : ""
              }, ${r.minutes_below_70} min under 70, coverage ${num1(r.coverage_pct)}%; ${r.reason_codes.join(", ")} (${r.code_source})`}</title>
            </circle>
          ),
        )}
        {/* the tolerance strip: one mark per answered check-in; a missing day is absent */}
        {(body.checkins ?? []).map((c) =>
          idx.has(c.date) ? (
            <rect key={c.date} x={x(idx.get(c.date)!) - 3} y={bot + 8} width="6" height="8" fill={GI_COLOR[c.gi] ?? "#737373"}>
              <title>{`${md(c.date)}: ${GI_LABEL[c.gi] ?? c.gi}`}</title>
            </rect>
          ) : null,
        )}
        {/* adherence: one dot per logged shot, hollow when not confirmed */}
        {(body.injections ?? []).map((j) => {
          const i = idx.get(j.time.slice(0, 10));
          return i === undefined ? null : (
            <circle key={j.time} cx={x(i)} cy={bot + 24} r="3" fill={j.confirmed ? "#60a5fa" : "none"} stroke="#60a5fa">
              <title>{`${md(j.time)} ${j.time.slice(11, 16)}: ${j.dose_label ?? "shot"}${j.confirmed ? "" : " (not confirmed)"}`}</title>
            </circle>
          );
        })}
        {n > 0 && <Ends first={md(sorted[0].night_date)} last={md(sorted[n - 1].night_date)} y={H - 2} />}
      </svg>
      <p className="text-xs text-neutral-500">
        Dots: each night&apos;s low point (hollow = coverage under 85%). Dashed line: baseline
        {body.baseline && base !== null ? `, median of ${body.baseline.nights} nights ${md(body.baseline.from)}–${md(body.baseline.to)}` : " (none)"}.
        Strip: stomach check-ins (green fine, amber rough, red can&apos;t eat). Blue: shots taken. Reason codes are marked logged
        or inferred in each night&apos;s detail; your doctor&apos;s card says what a shift means, not this picture.
      </p>
    </div>
  );
}

// ------------------------------------------------------------ buddy: events per week

export function BuddyChart({ rows }: { rows: BuddyRow[] }) {
  const sorted = [...rows].sort((a, b) => a.week.localeCompare(b.week));
  const sum = (k: keyof BuddyRow) => sorted.reduce((s, r) => s + (r[k] as number), 0);
  return (
    <div className="flex flex-col gap-2">
      <WeekBars
        weeks={sorted.map((r) => r.week)}
        stacks={[
          { key: "confirmed", color: "#38bdf8", values: sorted.map((r) => r.alerts_device_confirmed) },
          { key: "unconfirmed", color: "#737373", values: sorted.map((r) => r.alerts_unconfirmed) },
        ]}
        legend={`Buddy alerts per week: blue device-confirmed (${sum("alerts_device_confirmed")}), grey unconfirmed (${sum("alerts_unconfirmed")}).`}
      />
      <p className="text-xs text-neutral-400">
        In all: {sum("claims")} claimed, {sum("calls")} calls, {sum("treating")} marked treating, {sum("resolved")} resolved.
      </p>
    </div>
  );
}
