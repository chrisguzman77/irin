import { useEffect, useState } from "react";
import { PinRejected } from "../../lib/api";
import type { StateSnapshot } from "../../lib/contracts";
import { GI_ANSWERS, dayLabel, getCheckin, logShot, postCheckin, readCheckin, usePlanState, type Gi } from "../../lib/stepWatch";

// During a Step Watch (justin.md R2): the stomach strip (Fine / Rough / Can't
// eat), one tap, none pre-selected, shown when the Pi says a check-in is due or
// one was given today (a second tap replaces it); and, for a weekly GLP-1 /
// GIP-GLP-1 shot, "I took my shot", echoed with the dose label and confirmed
// before it is logged, like every insulin entry. The kiosk shows none of this.
export default function WatchToday({ snap, baseUrl, planState }: { snap: StateSnapshot; baseUrl: string; planState: Record<string, unknown> | null }) {
  const ps = usePlanState(baseUrl, snap, planState);
  const fromPi = readCheckin(snap.todays_checkin_status);
  const [answer, setAnswer] = useState<Gi | null>(null); // the Pi's answer to our tap
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null);
  const [asking, setAsking] = useState(false);
  const [lastShot, setLastShot] = useState<string | null>(null);
  const weekly = snap.active_plan && (snap.active_plan.drug_class === "glp1" || snap.active_plan.drug_class === "gip_glp1");
  const demo = ps.is_demo || snap.mode === "replay";

  // today's answer as the Pi holds it (a tap on another screen is not broadcast)
  useEffect(() => {
    if (!ps.active) return;
    let alive = true;
    getCheckin(baseUrl)
      .then((c) => alive && c && setAnswer(c.symptom_check_today))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [baseUrl, ps.active]);

  // the last logged shot, from the Pi's treatments (two weeks back)
  useEffect(() => {
    if (!ps.active || !weekly) return;
    let alive = true;
    fetch(`${baseUrl}/api/treatments?hours=336`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : []))
      .then((rows: { kind: string; timestamp: string; dose_label?: string | null }[]) => {
        const shots = rows.filter((t) => t.kind === "glp1_dose").sort((a, b) => b.timestamp.localeCompare(a.timestamp));
        if (alive) setLastShot(shots[0] ? `${shots[0].dose_label ?? "shot"} on ${dayLabel(shots[0].timestamp)}` : null);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [baseUrl, ps.active, weekly, busy]);

  if (!ps.active) return null;
  const today = answer ?? fromPi.symptom_check_today;
  const showStrip = fromPi.symptom_check_due || today !== null;

  const run = async (f: () => Promise<void>) => {
    if (busy) return;
    setBusy(true);
    setMsg(null);
    try {
      await f();
    } catch (e) {
      if (!(e instanceof PinRejected)) setMsg({ text: "Could not reach your Irin. Nothing was saved.", ok: false });
    } finally {
      setBusy(false);
    }
  };
  const tap = (gi: Gi) =>
    run(async () => {
      const res = await postCheckin(baseUrl, gi);
      if (res.ok) setAnswer(res.status.symptom_check_today ?? gi);
      else setMsg({ text: res.reason, ok: false });
    });
  const shot = () =>
    run(async () => {
      const res = await logShot(baseUrl, ps.dose_label ?? "");
      setAsking(false);
      setMsg(res.ok ? { text: `Logged: ${ps.drug_label ?? ""} ${ps.dose_label ?? ""}.`.replace(/\s+/g, " "), ok: true } : { text: res.reason, ok: false });
    });

  if (!showStrip && !weekly) return null;
  return (
    <section className="rounded-xl border border-neutral-800 bg-neutral-950 p-4 flex flex-col gap-3" aria-label="step watch today">
      <div className="flex items-center gap-2">
        <span className="text-xs uppercase tracking-wider text-neutral-500 flex-1">Step Watch · today</span>
        {demo && <span className="bg-amber-400 text-black text-xs font-bold px-2 py-0.5 rounded">DEMO</span>}
      </div>
      {showStrip && (
        <>
          <p className="text-lg">How is your stomach today?</p>
          <div className="grid grid-cols-3 gap-2">
            {GI_ANSWERS.map(([gi, label]) => (
              <button
                key={gi}
                type="button"
                disabled={busy}
                aria-pressed={today === gi}
                onClick={() => tap(gi)}
                className={`rounded-lg px-3 py-3 font-medium disabled:opacity-40 ${
                  today === gi ? "bg-white text-black" : "bg-neutral-800 text-neutral-100"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          {today && <p className="text-sm text-emerald-400">Saved: {GI_ANSWERS.find(([g]) => g === today)?.[1]}. Tap another to change it.</p>}
        </>
      )}
      {weekly && ps.dose_label && (
        <div className="flex flex-col gap-2">
          {asking ? (
            <div className="rounded-lg bg-neutral-900 p-3 flex flex-col gap-2">
              <p>
                Log your {ps.drug_label} <strong>{ps.dose_label}</strong> shot, taken now?
              </p>
              <div className="flex gap-2">
                <button type="button" onClick={() => setAsking(false)} className="flex-1 rounded-lg bg-neutral-800 py-2">
                  Cancel
                </button>
                <button type="button" disabled={busy} onClick={shot} className="flex-1 rounded-lg bg-white text-black font-semibold py-2 disabled:opacity-40">
                  Confirm
                </button>
              </div>
            </div>
          ) : (
            <button type="button" onClick={() => setAsking(true)} className="rounded-lg bg-neutral-800 py-3 font-medium">
              I took my shot
            </button>
          )}
          <p className="text-xs text-neutral-500">{lastShot ? `Last logged: ${lastShot}.` : "No shot logged in the last two weeks."}</p>
        </div>
      )}
      {msg && <p className={`text-sm ${msg.ok ? "text-emerald-400" : "text-amber-300"}`}>{msg.text}</p>}
    </section>
  );
}
