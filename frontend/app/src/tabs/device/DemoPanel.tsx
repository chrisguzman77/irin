import { useCallback, useEffect, useState, type ReactNode } from "react";
import type { components } from "../../types/pi";
import { deviceFetch, PinRejected } from "../../lib/api";
import type { StateSnapshot } from "../../lib/contracts";
import {
  basalNudge, buddyRung, injectLow, latestEvaluations, listScenarios, playScenario, seek, sendEvaluatedCard, setBrainOnly, setPaused, setSpeed,
  sparkOffer, type Evaluation, type ScenarioList, type SeekResult,
} from "../../lib/demo";
import { sendSampleCard, type SampleFixture } from "../../lib/cards";

// Step 8: the demo panel, stagecraft reached from a small button, never a
// main tab. The LIVE/DEMO switch drives POST /api/mode (PIN-gated); the
// screen changes only when the Pi's mode_change arrives. Everything below
// the switch works only in demo mode (the Pi 404s it in live) and is greyed
// in live mode.
type ModeRequest = components["schemas"]["ModeRequest"];

const SPEEDS = [1, 10, 60, 120] as const;
const INJECT_MIN = 39;
const INJECT_MAX = 401;

// The noise budget's verdicts (backend/app/rounds/noise.py Verdict.reason), in words.
const BUDGET: Record<string, string> = {
  sent: "sent",
  digest: "held for the digest (green never interrupts)",
  interval: "held: one card of this kind per 14 days",
  red_cap: "held: one red per kind per 12 hours",
  red_duplicate: "held: this event's red was already sent",
  watch: "held: a Step Watch is running",
  insufficient: "held: not enough data",
  per_step: "held: one per step",
  per_plan: "held: once per plan",
  program_day: "held: the other program sent today",
};

/** "Mar 22 09:00" from the Pi's naive local time, read as text */
function clockText(iso: string | null | undefined): string {
  if (!iso) return "";
  const M = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return y ? `${M[m - 1]} ${d} ${iso.slice(11, 16)}` : "";
}

/** The Pi's seek answer, in words. */
function seekText(r: SeekResult): string {
  const seeded = Object.entries(r.seeded ?? {})
    .filter(([, n]) => n > 0)
    .map(([k, n]) => `${n} ${k.replace(/_/g, " ")}`)
    .join(", ");
  return `Now at ${clockText(r.clock)} in ${r.scenario.replace(/_/g, " ")}: ${r.nights_built} nights built, ${
    r.mornings_evaluated
  } mornings evaluated${seeded ? `; seeded ${seeded}` : ""}.`;
}

const SEND_OUTCOME: Record<string, string> = {
  sent: "Card sent to your paired doctor's inbox.",
  unsent: "Card saved; the relay did not take it yet, your Irin will retry.",
  no_recipient: "Card saved but not sent: no doctor is paired in demo mode. Pair one first.",
};

// Calm, brand-coloured building blocks (irin-* in index.css). Alarm red,
// DEMO amber and the red/amber/green card statuses keep their own colours.
const btn = "rounded-full px-4 py-2 font-semibold transition-colors duration-300 disabled:opacity-40";
const primary = `${btn} bg-gradient-to-r from-irin-leaf to-irin-mint text-irin-ink shadow-md shadow-black/20 active:brightness-110`;
const secondary = `${btn} bg-irin-ink/70 border border-irin-leaf/40 text-irin-cream active:bg-irin-line`;
const field = "bg-irin-ink/80 border border-irin-leaf/30 rounded-xl px-3 py-2 text-irin-cream focus:outline-none focus:border-irin-mint";

/** A soft two-tone wave, the brand's leaf and mint layered, used as a divider. */
function Wave({ flip = false }: { flip?: boolean }) {
  return (
    <svg viewBox="0 0 400 40" preserveAspectRatio="none" aria-hidden className={`block w-full h-6 ${flip ? "rotate-180" : ""}`}>
      <path d="M0 22 C 80 4, 160 40, 240 20 S 360 6, 400 18 L400 40 L0 40 Z" className="fill-irin-leaf/25" />
      <path d="M0 30 C 90 16, 170 44, 260 28 S 370 18, 400 26 L400 40 L0 40 Z" className="fill-irin-mint/15" />
    </svg>
  );
}

// Each group carries one tint so the colour flows leaf -> mint -> sage down the page.
const TINTS = {
  leaf: "from-irin-leaf/20",
  mint: "from-irin-mint/15",
  sage: "from-irin-sage/15",
} as const;

/** A titled group of related controls, washed with its tint at the top. */
function Group({ title, note, tint, children }: { title: string; note?: string; tint: keyof typeof TINTS; children: ReactNode }) {
  return (
    <div className={`rounded-3xl bg-gradient-to-b ${TINTS[tint]} via-irin-surface to-irin-surface border border-irin-line/60 px-4 pt-4 pb-1 flex flex-col`}>
      <div className="flex items-center gap-2">
        <span className="h-2 w-2 rounded-full bg-gradient-to-br from-irin-leaf to-irin-mint" />
        <h3 className="text-xs font-semibold uppercase tracking-[0.18em] text-irin-mint">{title}</h3>
      </div>
      {note && <p className="text-sm text-irin-sage mt-1">{note}</p>}
      <div className="flex flex-col divide-y divide-irin-line/50">{children}</div>
    </div>
  );
}

/** One control. With `side`, a single action sits to the right of its name
 *  (one row); otherwise the inputs sit full width under the name. */
function Control({ title, hint, side, children }: { title: string; hint?: string; side?: ReactNode; children?: ReactNode }) {
  return (
    <div className="py-3 flex flex-col gap-2">
      <div className="flex items-center gap-3">
        <div className="flex flex-col flex-1 min-w-0">
          <span className="text-irin-cream font-medium">{title}</span>
          {hint && <span className="text-xs text-irin-sage">{hint}</span>}
        </div>
        {side && <div className="shrink-0">{side}</div>}
      </div>
      {children}
    </div>
  );
}

export default function DemoPanel({ snap, baseUrl, onClose }: { snap: StateSnapshot | null; baseUrl: string; onClose: () => void }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ text: string; error: boolean } | null>(null);
  const [info, setInfo] = useState<ScenarioList | null>(null);
  const [picked, setPicked] = useState("");
  const [injectValue, setInjectValue] = useState("55");
  const [seekStep, setSeekStep] = useState("2");
  const [seekDay, setSeekDay] = useState("8");
  const [seekDate, setSeekDate] = useState("");
  const [evals, setEvals] = useState<Evaluation[] | null>(null);
  const loadEvals = useCallback(async () => {
    try {
      setEvals(await latestEvaluations(baseUrl));
    } catch {
      setEvals(null);
    }
  }, [baseUrl]);
  const mode = snap?.mode;
  const demo = mode === "replay";

  const refresh = useCallback(async () => {
    try {
      const l = await listScenarios(baseUrl);
      setInfo(l);
      setPicked((p) => p || l.current || l.scenarios[0] || "");
    } catch {
      setInfo(null);
    }
  }, [baseUrl]);

  useEffect(() => {
    refresh();
  }, [mode, refresh]);

  /** Run one PIN-gated call; the message says what the Pi answered. */
  const run = async (call: () => Promise<string>, done: string, after?: () => void) => {
    if (busy) return;
    setBusy(true);
    setMsg(null);
    try {
      const err = await call();
      if (err) setMsg({ text: err, error: true });
      else {
        if (done) setMsg({ text: done, error: false }); // "" = the call set its own message
        after?.();
      }
    } catch (e) {
      if (!(e instanceof PinRejected)) setMsg({ text: "Could not reach your Irin.", error: true });
    } finally {
      setBusy(false);
    }
  };

  const switchTo = (m: ModeRequest["mode"]) => {
    if (m === mode) return;
    const body: ModeRequest = { mode: m };
    run(
      async () => {
        const res = await deviceFetch(baseUrl, "/api/mode", { method: "POST", body: JSON.stringify(body) });
        return res.ok ? "" : `Your Irin refused the switch (${res.status}).`;
      },
      m === "replay" ? "Switching to demo…" : "Switching to live…",
    );
  };

  const seg = (m: ModeRequest["mode"], label: string, on: string) => (
    <button
      type="button"
      disabled={busy}
      aria-pressed={mode === m}
      onClick={() => switchTo(m)}
      className={`flex-1 py-3 text-lg font-black tracking-wider rounded-full ${mode === m ? on : "text-irin-sage"} disabled:opacity-60`}
    >
      {label}
    </button>
  );

  const inject = Number(injectValue);
  const injectOk = injectValue !== "" && Number.isFinite(inject) && inject >= INJECT_MIN && inject <= INJECT_MAX;
  const speed = info?.speed;
  const paused = info?.paused ?? false; // the Pi's own answer (GET /api/demo/scenarios)
  const brainOnly = info?.brain_only ?? false;
  const stepN = seekStep === "" ? null : Number(seekStep);
  const dayN = Number(seekDay);
  const seekOk =
    /^\d{4}-\d{2}-\d{2}$/.test(seekDate) ||
    (seekDay !== "" && Number.isInteger(dayN) && dayN >= 1 && (stepN === null || (Number.isInteger(stepN) && stepN >= 1)));

  return (
    <section className="flex flex-col gap-4 pb-24">
      {/* hero: the mode switch on a leaf-to-ink wash with the brand wave under it */}
      <div className="rounded-3xl overflow-hidden border border-irin-line/60 bg-gradient-to-br from-irin-leaf/35 via-irin-surface to-irin-ink">
        <div className="px-4 pt-4 flex items-start justify-between gap-3">
          <div>
            <h2 className="text-xl font-semibold text-irin-cream">Demo panel</h2>
            <p className="text-sm text-irin-sage">Stage controls for showing Irin with replayed nights.</p>
          </div>
          <button type="button" onClick={onClose} className={secondary}>
            Close
          </button>
        </div>
        <div className="px-4 pt-4 flex flex-col gap-2">
          <div className="flex gap-1 p-1 rounded-full bg-irin-ink/80 border border-irin-line">
            {seg("nightscout", "LIVE", "bg-gradient-to-r from-irin-leaf to-irin-mint text-irin-ink")}
            {seg("replay", "DEMO", "bg-amber-400 text-black")}
          </div>
          <p className="text-sm text-irin-sage text-center">
            {mode === undefined
              ? "Waiting for your Irin…"
              : demo
                ? `Demo: replayed data, badged DEMO on every screen.${info?.current ? ` Playing ${info.current.replace(/_/g, " ")} at ${speed}×.` : ""}${
                    info?.clock ? ` Replay clock ${clockText(info.clock)}.` : ""
                  }`
                : "Live: real readings. Demo controls are off."}
          </p>
        </div>
        <Wave />
      </div>

      <fieldset disabled={!demo || busy} className={`flex flex-col gap-4 transition-opacity duration-300 ${demo ? "" : "opacity-40"}`}>
        <legend className="sr-only">{demo ? "Demo controls" : "Demo controls (demo mode only)"}</legend>
        {!demo && <p className="text-sm text-irin-sage text-center">Switch to DEMO to use the controls below.</p>}

        <Group title="Playback" note="Choose a recorded night and control how it plays." tint="leaf">
          <Control title="Scenario" hint="Plays from its first reading.">
            <div className="flex gap-2">
              <select
                aria-label="Scenario"
                className={`flex-1 min-w-0 ${field}`}
                value={picked}
                onChange={(e) => setPicked(e.target.value)}
              >
                {(info?.scenarios ?? []).map((s) => (
                  <option key={s} value={s}>
                    {s.replace(/_/g, " ")}
                    {s === info?.current ? " (playing)" : ""}
                  </option>
                ))}
              </select>
              <button
                type="button"
                disabled={!picked}
                className={`${primary} px-6`}
                onClick={() => run(() => playScenario(baseUrl, picked), `Playing ${picked.replace(/_/g, " ")} from the start.`, () => {
                  refresh();
                })}
              >
                Play
              </button>
            </div>
          </Control>

          <Control title="Replay speed" hint="Clock-minutes per real minute.">
            <div className="grid grid-cols-4 gap-1 p-1 rounded-full bg-irin-ink/80 border border-irin-line">
              {SPEEDS.map((s) => (
                <button
                  key={s}
                  type="button"
                  aria-pressed={speed === s}
                  className={`${btn} py-1.5 ${speed === s ? "bg-gradient-to-r from-irin-leaf to-irin-mint text-irin-ink" : "text-irin-sage"}`}
                  onClick={() => run(() => setSpeed(baseUrl, s), `Speed ${s}×.`, refresh)}
                >
                  {s}×
                </button>
              ))}
            </div>
          </Control>

          <Control
            title="Sensor feed"
            hint="While paused, the reading goes stale after 15 clock-minutes."
            side={
              <button
                type="button"
                aria-pressed={paused}
                className={paused ? `${btn} bg-amber-400 text-black` : secondary}
                onClick={() =>
                  run(() => setPaused(baseUrl, !paused), paused ? "Feed resumed." : "Feed paused.", refresh)
                }
              >
                {paused ? "Resume feed" : "Pause feed"}
              </button>
            }
          />
        </Group>

        <Group title="Show a moment" note="Make something happen right now." tint="mint">
          <Control title="Inject a low" hint="One falling reading now; the scenario itself stays clean.">
            <div className="flex gap-2 items-center">
              <div className="flex items-center rounded-xl bg-irin-ink/80 border border-irin-leaf/30 pr-3 focus-within:border-irin-mint">
                <input
                  aria-label="Injected glucose (mg/dL)"
                  className="w-16 bg-transparent px-3 py-2 text-irin-cream focus:outline-none"
                  type="number"
                  inputMode="numeric"
                  min={INJECT_MIN}
                  max={INJECT_MAX}
                  value={injectValue}
                  onChange={(e) => setInjectValue(e.target.value)}
                />
                <span className="text-irin-sage text-sm">mg/dL</span>
              </div>
              <button
                type="button"
                disabled={!injectOk}
                className={`${btn} flex-1 bg-red-500 text-white`}
                onClick={() => run(() => injectLow(baseUrl, inject), `Injected ${inject} mg/dL.`)}
              >
                Inject
              </button>
            </div>
            {!injectOk && <span className="text-xs text-red-400">Between {INJECT_MIN} and {INJECT_MAX}.</span>}
          </Control>

          <Control
            title="Basal-time nudge"
            hint="Sets basal time to 61 min ago; restored when you switch to live."
            side={
              <button
                type="button"
                className={secondary}
                onClick={() => run(() => basalNudge(baseUrl), "Basal nudge triggered.")}
              >
                Show nudge
              </button>
            }
          />
        </Group>

        <Group title="Doctor · Irin Rounds" note="Move through time and send cards to the paired demo doctor." tint="sage">
          <Control title="Jump ahead" hint="Step 2 day 8 shows the step-2 check. Leave step empty for day D of the scenario. A date wins over both.">
            <div className="grid grid-cols-2 gap-2">
              <label className="flex items-center gap-2 rounded-xl bg-irin-ink/80 border border-irin-leaf/30 px-3 focus-within:border-irin-mint">
                <span className="text-irin-sage text-sm">Step</span>
                <input
                  aria-label="Plan step"
                  className="w-full min-w-0 bg-transparent py-2 text-irin-cream focus:outline-none"
                  type="number"
                  inputMode="numeric"
                  min={1}
                  value={seekStep}
                  onChange={(e) => setSeekStep(e.target.value)}
                />
              </label>
              <label className="flex items-center gap-2 rounded-xl bg-irin-ink/80 border border-irin-leaf/30 px-3 focus-within:border-irin-mint">
                <span className="text-irin-sage text-sm">Day</span>
                <input
                  aria-label="Day"
                  className="w-full min-w-0 bg-transparent py-2 text-irin-cream focus:outline-none"
                  type="number"
                  inputMode="numeric"
                  min={1}
                  value={seekDay}
                  onChange={(e) => setSeekDay(e.target.value)}
                />
              </label>
            </div>
            <div className="flex gap-2 items-center">
              <label className="flex flex-1 min-w-0 items-center gap-2 rounded-xl bg-irin-ink/80 border border-irin-leaf/30 px-3 focus-within:border-irin-mint">
                <span className="text-irin-sage text-sm whitespace-nowrap">or date</span>
                <input
                  aria-label="Seek date"
                  className="flex-1 min-w-0 bg-transparent py-2 text-irin-cream focus:outline-none"
                  type="date"
                  value={seekDate}
                  onChange={(e) => setSeekDate(e.target.value)}
                />
              </label>
              {seekDate && (
                <button type="button" className="text-sm text-irin-sage underline" onClick={() => setSeekDate("")}>
                  clear
                </button>
              )}
            </div>
            <button
              type="button"
              disabled={!seekOk}
              className={`${primary} w-full py-2.5`}
              onClick={() =>
                run(async () => {
                  const r = await seek(
                    baseUrl,
                    seekDate ? { date: seekDate } : stepN === null ? { day: dayN } : { step: stepN, day: dayN },
                  );
                  if (!r.ok) return r.reason;
                  setMsg({ text: seekText(r.body), error: false });
                  return "";
                }, "", () => {
                  refresh();
                  loadEvals();
                })
              }
            >
              Jump
            </button>
            <span className="text-xs text-irin-sage">
              Lands at 09:00 of that day; the catch-up builds every night, question and card up to it. Forward only.
              {info?.companion ? "" : " This scenario has no companion (no plan to seek by step)."}
            </span>
          </Control>

          <Control
            title="Irin Brain only"
            hint="Cards change confidence labels, never blank a row."
            side={
              <button
                type="button"
                role="switch"
                aria-checked={brainOnly}
                aria-pressed={brainOnly}
                aria-label={brainOnly ? "Brain only (tap for Bedside)" : "Bedside (tap for Brain only)"}
                className="flex items-center gap-2"
                onClick={() =>
                  run(async () => {
                    const r = await setBrainOnly(baseUrl, !brainOnly);
                    if (!r.ok) return r.reason;
                    setMsg({
                      text: r.body.brain_only
                        ? "Brain only: presence, alarm hardware events and logged context are ignored."
                        : "Bedside: every signal counts again.",
                      error: false,
                    });
                    return "";
                  }, "", refresh)
                }
              >
                <span className="text-sm text-irin-sage">{brainOnly ? "Brain only" : "Bedside"}</span>
                <span
                  className={`relative h-7 w-12 rounded-full transition-colors duration-300 ${
                    brainOnly ? "bg-gradient-to-r from-irin-leaf to-irin-mint" : "bg-irin-ink border border-irin-line"
                  }`}
                >
                  <span
                    className={`absolute top-1 h-5 w-5 rounded-full bg-irin-cream shadow transition-all duration-300 ${brainOnly ? "left-6" : "left-1"}`}
                  />
                </span>
              </button>
            }
          />

          <Control title="Send the real card" hint="Today's card from the engine: Brain only, or Bedside and Brain side by side.">
            <div className="grid grid-cols-2 gap-2">
              {(
                [
                  ["brain", "Brain-only"],
                  ["both", "Bedside + Brain"],
                ] as const
              ).map(([m, label]) => (
                <button
                  key={m}
                  type="button"
                  className={secondary}
                  onClick={() =>
                    run(async () => {
                      const r = await sendEvaluatedCard(baseUrl, m);
                      if (!r.ok) return r.reason;
                      const lines = r.body.cards.map(
                        (c) => `${c.card_id.includes("brain") ? "Brain" : "Bedside"} ${c.kind.replace(/_/g, " ")}: ${SEND_OUTCOME[c.status] ?? c.status}`,
                      );
                      setMsg({ text: lines.join(" ") || "No card was due.", error: r.body.cards.some((c) => c.status === "no_recipient") });
                      return "";
                    }, "")
                  }
                >
                  {label}
                </button>
              ))}
            </div>
          </Control>

          <Control title="Send a sample card" hint="A sample card, badged DEMO, sealed to the paired demo doctor.">
            <div className="grid grid-cols-2 gap-2">
              {([
                ["signal_card_standing", "Basal Check"],
                ["signal_card_step", "Step check"],
              ] as [SampleFixture, string][]).map(([f, label]) => (
                <button
                  key={f}
                  type="button"
                  className={secondary}
                  onClick={() =>
                    run(async () => {
                      const r = await sendSampleCard(baseUrl, f);
                      if (!r.ok) return r.reason;
                      setMsg({ text: SEND_OUTCOME[r.status] ?? `Card ${r.status}.`, error: r.status === "no_recipient" });
                      return "";
                    }, "")
                  }
                >
                  {label}
                </button>
              ))}
            </div>
          </Control>

          <Control
            title="Simulate a Spark offer"
            hint="Impiricus Spark (simulated) offers the scenario's plan; confirm it on the takeover."
            side={
              <button
                type="button"
                className={secondary}
                onClick={() =>
                  run(async () => {
                    const r = await sparkOffer(baseUrl);
                    if (!r.ok) return r.reason;
                    setMsg({
                      text: `Offer ${r.body.status} (plan ${r.body.plan_id}). Confirm it on the takeover with your code to start the watch.`,
                      error: false,
                    });
                    return "";
                  }, "")
                }
              >
                Simulate
              </button>
            }
          />

          <Control
            title="Latest evaluations"
            hint="Each Standing card's newest evaluation and what the noise budget did with it."
            side={
              <button type="button" className={secondary} onClick={loadEvals}>
                {evals ? "Refresh" : "Show"}
              </button>
            }
          >
            {evals && evals.length === 0 && <span className="text-sm text-irin-sage">No evaluation yet.</span>}
            {evals?.map((e) => (
              <div key={e.kind} className="text-sm flex flex-col gap-0.5 rounded-xl bg-irin-ink/70 px-3 py-2">
                <span className="text-irin-cream">
                  {e.kind.replace(/_/g, " ")} · <span className={e.status === "red" ? "text-red-400" : e.status === "amber" ? "text-amber-300" : e.status === "green" ? "text-emerald-400" : "text-irin-sage"}>{e.status || "—"}</span>
                  {e.budget ? ` · ${BUDGET[e.budget] ?? e.budget}` : ""}
                  {e.sent ? " · sent" : ""}
                </span>
                {e.headline && <span className="text-irin-sage">{e.headline}</span>}
              </div>
            ))}
          </Control>
        </Group>

        <Group title="Night Buddy" tint="leaf">
          <Control
            title="Trigger the buddy rung"
            hint="Alerts the paired buddy as if an alarm went unanswered."
            side={
              <button
                type="button"
                className={secondary}
                onClick={() =>
                  run(async () => {
                    const r = await buddyRung(baseUrl);
                    if (!r.ok) return r.reason;
                    setMsg({ text: `Buddy rung: ${r.body.status}.`, error: false });
                    return "";
                  }, "")
                }
              >
                Trigger
              </button>
            }
          />
        </Group>
      </fieldset>

      {/* the Pi's answer to the last tap, pinned near the thumb so it is seen wherever the control was */}
      {msg && (
        <p
          role="status"
          className={`sticky bottom-3 rounded-2xl px-4 py-3 text-sm shadow-lg shadow-black/40 border backdrop-blur ${
            msg.error ? "bg-red-950/95 border-red-800 text-red-200" : "bg-irin-ink/95 border-irin-leaf/60 text-irin-mint"
          }`}
        >
          {msg.text}
        </p>
      )}
    </section>
  );
}
