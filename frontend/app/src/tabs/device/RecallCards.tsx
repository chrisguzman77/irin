import { useEffect, useState } from "react";
import { PinRejected } from "../../lib/api";
import {
  ANSWERS, SYMPTOM_ANSWERS, answerLabel, answerRecall, markAnswered, noonAfter, useAnswered, usePiClock,
  type LowEvent, type RecallAnswer,
} from "../../lib/recall";

// R2: morning recall cards. "At 2:47 AM you were 58 for about 25 minutes. Do
// you remember that?" with the curve around the low and four one-tap answers,
// none pre-selected. Carbs logged near the low: "You logged carbs at 3:05.
// Did you feel symptoms?" (yes / no). The Pi's answer is shown only after it
// accepted it; after noon (Pi clock) the card is gone and the Pi records "no
// answer", never "fine".

const pad = (n: number) => String(n).padStart(2, "0");
/** "2:47 AM" from a naive Pi timestamp (read as text, no timezone shift) */
function clock12(iso: string): string {
  const [h, m] = iso.slice(11, 16).split(":").map(Number);
  return `${h % 12 === 0 ? 12 : h % 12}:${pad(m)} ${h < 12 ? "AM" : "PM"}`;
}
const ms = (iso: string) => new Date(iso).getTime();

interface Point {
  t: number;
  v: number;
}

function Curve({ points, nadirAt }: { points: Point[]; nadirAt: number }) {
  if (points.length < 3) return null;
  const W = 300, H = 90, P = 6;
  const t0 = points[0].t, t1 = points[points.length - 1].t;
  const hi = Math.max(160, ...points.map((p) => p.v)), lo = Math.min(40, ...points.map((p) => p.v));
  const x = (t: number) => P + ((t - t0) / Math.max(1, t1 - t0)) * (W - 2 * P);
  const y = (v: number) => P + (1 - (v - lo) / (hi - lo)) * (H - 2 * P);
  const nadir = points.reduce((a, b) => (Math.abs(b.t - nadirAt) < Math.abs(a.t - nadirAt) ? b : a));
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-24" role="img" aria-label="glucose around the low">
      <rect x={P} y={y(70)} width={W - 2 * P} height={H - P - y(70)} fill="#7f1d1d" opacity="0.35" />
      <line x1={P} x2={W - P} y1={y(70)} y2={y(70)} stroke="#f87171" strokeDasharray="4 3" strokeWidth="1" />
      <polyline fill="none" stroke="#e5e5e5" strokeWidth="2" points={points.map((p) => `${x(p.t)},${y(p.v)}`).join(" ")} />
      <circle cx={x(nadir.t)} cy={y(nadir.v)} r="4" fill="#f87171" />
    </svg>
  );
}

function RecallCard({ low, baseUrl, piNow, demo }: { low: LowEvent; baseUrl: string; piNow: number | null; demo: boolean }) {
  const answered = useAnswered().get(low.low_event_id);
  const [points, setPoints] = useState<Point[]>([]);
  const [carbsAt, setCarbsAt] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [changing, setChanging] = useState(false);
  const nadirAt = ms(low.nadir_at);

  // the curve: an hour either side of the nadir, from the Pi's history
  useEffect(() => {
    if (piNow === null) return;
    const minutes = Math.min(1440, Math.ceil((piNow - nadirAt) / 60000) + 60);
    if (minutes < 1) return;
    let alive = true;
    fetch(`${baseUrl}/api/history?minutes=${minutes}`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : []))
      .then((rows: { timestamp: string; glucose_mgdl: number }[]) => {
        if (!alive) return;
        setPoints(
          rows
            .map((r) => ({ t: ms(r.timestamp), v: r.glucose_mgdl }))
            .filter((p) => p.t >= nadirAt - 3600000 && p.t <= nadirAt + 3600000),
        );
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
    // piNow only decides how far back to fetch: fetch once per card
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [baseUrl, low.low_event_id]);

  // when carbs were logged near the low, name the time
  useEffect(() => {
    if (!low.carbs_logged_within_30min) return;
    let alive = true;
    fetch(`${baseUrl}/api/treatments?hours=48`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : []))
      .then((rows: { timestamp: string; kind: string }[]) => {
        const c = rows.find((t) => t.kind === "carbs" && Math.abs(ms(t.timestamp) - nadirAt) <= 30 * 60000);
        if (alive && c) setCarbsAt(clock12(c.timestamp));
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [baseUrl, low.low_event_id, low.carbs_logged_within_30min, nadirAt]);

  const send = async (a: RecallAnswer) => {
    if (busy) return;
    setBusy(true);
    setMsg(null);
    try {
      const res = await answerRecall(baseUrl, low.low_event_id, a);
      if (res.ok) {
        markAnswered(low.low_event_id, a);
        setChanging(false);
      } else setMsg(res.reason);
    } catch (e) {
      if (!(e instanceof PinRejected)) setMsg("Could not reach your Irin. Nothing was saved.");
    } finally {
      setBusy(false);
    }
  };

  const symptomsOnly = low.carbs_logged_within_30min;
  const choices = symptomsOnly ? SYMPTOM_ANSWERS : ANSWERS;
  const minutes = low.minutes_below_70;
  return (
    <li className="rounded-xl border border-neutral-800 bg-neutral-950 p-4 flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <span className="text-xs uppercase tracking-wider text-neutral-500">Last night</span>
        {demo && <span className="bg-amber-400 text-black text-xs font-bold px-2 py-0.5 rounded">DEMO</span>}
      </div>
      <p className="text-lg leading-snug">
        At {clock12(low.nadir_at)} you were {Math.round(low.nadir_mgdl)}
        {minutes > 0 ? ` for about ${minutes} minute${minutes === 1 ? "" : "s"}` : ""}.
        {symptomsOnly ? (
          <> {carbsAt ? `You logged carbs at ${carbsAt}.` : "You logged carbs soon after."} Did you feel symptoms?</>
        ) : (
          <> Do you remember that?</>
        )}
      </p>
      <Curve points={points} nadirAt={nadirAt} />
      {answered && !changing ? (
        <div className="flex items-center gap-2">
          <span className="text-emerald-400 text-sm font-semibold flex-1">
            Saved: {(symptomsOnly ? SYMPTOM_ANSWERS.find(([k]) => k === answered)?.[1] : null) ?? answerLabel(answered)}
          </span>
          <button type="button" className="text-sm text-neutral-400 underline" onClick={() => setChanging(true)}>
            Change
          </button>
        </div>
      ) : (
        <div className={`grid gap-2 ${symptomsOnly ? "grid-cols-2" : "grid-cols-1 sm:grid-cols-2"}`}>
          {choices.map(([a, label]) => (
            <button
              key={a}
              type="button"
              disabled={busy}
              onClick={() => send(a)}
              className="rounded-lg bg-neutral-800 text-neutral-100 px-3 py-3 text-left font-medium disabled:opacity-40"
            >
              {label}
            </button>
          ))}
        </div>
      )}
      {msg && <p className="text-sm text-red-400">{msg}</p>}
    </li>
  );
}

export default function RecallCards({
  lows, baseUrl, demo,
}: {
  lows: LowEvent[];
  baseUrl: string;
  demo: boolean;
}) {
  const piNow = usePiClock(lows.length ? baseUrl : null);
  // after noon of that morning the question is closed (the Pi records "no answer")
  if (piNow === null) return null; // no trusted "now" yet: never show a question that may have closed
  const open = lows.filter((l) => piNow < noonAfter(l.night_date));
  if (open.length === 0) return null;
  return (
    <section className="flex flex-col gap-3 mb-4" aria-label="morning questions">
      <h3 className="text-sm uppercase tracking-wider text-neutral-400">
        {open.length === 1 ? "A question about last night" : `${open.length} questions about last night`}
      </h3>
      <ul className="flex flex-col gap-3">
        {open.map((l) => (
          <RecallCard key={l.low_event_id} low={l} baseUrl={baseUrl} piNow={piNow} demo={demo || !!l.is_demo} />
        ))}
      </ul>
    </section>
  );
}
