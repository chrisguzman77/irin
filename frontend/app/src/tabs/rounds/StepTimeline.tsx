import { useEffect, useState } from "react";
import type { StateSnapshot } from "../../lib/contracts";
import { GRADUATION_GREEN_WEEKS, dayLabel, getPlan, usePlanState, type TitrationPlan } from "../../lib/stepWatch";

// The step timeline on the Rounds tab during a watch (justin.md R3): the
// planned steps with their start dates (a doctor's hold has already moved the
// later dates on the Pi), where you are, and what comes next. The plan's
// steps come from GET /api/rounds/plan (PIN), refetched on every plan_state.
export default function StepTimeline({ snap, baseUrl, planState }: { snap: StateSnapshot; baseUrl: string; planState: Record<string, unknown> | null }) {
  const ps = usePlanState(baseUrl, snap, planState);
  const [plan, setPlan] = useState<TitrationPlan | null>(null);
  const key = JSON.stringify(ps);

  useEffect(() => {
    if (!ps.active) {
      setPlan(null);
      return;
    }
    let alive = true;
    getPlan(baseUrl)
      .then((p) => alive && setPlan(p))
      .catch(() => {});
    return () => {
      alive = false;
    };
    // refetch whenever the Pi's plan_state changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [baseUrl, key]);

  if (!ps.active) return null;
  const steps = plan?.steps ?? [];
  const last = steps.length ? steps[steps.length - 1].index : null;
  const onLast = ps.step_index !== null && ps.step_index === last;
  const next = steps.find((s) => ps.step_index !== null && s.index === ps.step_index + 1);

  return (
    <section className="rounded-xl border border-neutral-800 bg-neutral-950 p-4 flex flex-col gap-3" aria-label="step watch">
      <div className="flex items-center gap-2">
        <h3 className="text-sm uppercase tracking-wider text-neutral-400 flex-1">Step Watch</h3>
        {(ps.is_demo || snap.mode === "replay") && <span className="bg-amber-400 text-black text-xs font-bold px-2 py-0.5 rounded">DEMO</span>}
      </div>
      <p className="text-lg">
        {ps.drug_label} <strong>{ps.dose_label ?? "—"}</strong>
        {ps.day_in_step !== null && <span className="text-neutral-400"> · day {ps.day_in_step} of this step</span>}
      </p>
      {next ? (
        <p className="text-sm text-neutral-300">
          Next step: {next.dose_label} on {dayLabel(next.planned_start)}. Your doctor looks at the step gate 3 days before and can hold it.
        </p>
      ) : ps.next_step_on ? (
        <p className="text-sm text-neutral-300">Next step on {dayLabel(ps.next_step_on)}.</p>
      ) : onLast ? (
        <p className="text-sm text-neutral-300">
          Maintenance dose: {Math.min(ps.green_weeks ?? 0, GRADUATION_GREEN_WEEKS)} of {GRADUATION_GREEN_WEEKS} green weeks toward the end of the watch.
        </p>
      ) : null}
      {steps.length > 0 && (
        <ol className="flex flex-col gap-1">
          {steps.map((s) => {
            const now = s.index === ps.step_index;
            const past = ps.step_index !== null && s.index < ps.step_index;
            return (
              <li key={s.index} className={`flex items-center gap-3 text-sm ${now ? "text-white font-semibold" : past ? "text-neutral-500" : "text-neutral-300"}`}>
                <span className={`w-2.5 h-2.5 rounded-full ${now ? "bg-white" : past ? "bg-neutral-600" : "border border-neutral-500"}`} />
                <span className="flex-1">{s.dose_label}</span>
                <span className="tabular-nums">{past || now ? "from" : "planned"} {dayLabel(s.planned_start)}</span>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
