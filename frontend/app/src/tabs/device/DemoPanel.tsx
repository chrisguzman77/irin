import { useCallback, useEffect, useState, type ReactNode } from "react";
import type { components } from "../../types/pi";
import { deviceFetch, PinRejected } from "../../lib/api";
import type { StateSnapshot } from "../../lib/contracts";
import {
  basalNudge, buddyRung, injectLow, listScenarios, playScenario, seek, sendEvaluatedCard, setBrainOnly, setPaused, setSpeed,
  sparkOffer, type ScenarioList, type SeekResult,
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

const btn = "rounded-lg px-3 py-2 font-semibold disabled:opacity-40";

function Control({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return (
    <div className="rounded-lg border border-neutral-800 px-3 py-3 flex flex-col gap-2">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-neutral-200 font-medium">{title}</span>
        {hint && <span className="text-xs text-neutral-500 text-right">{hint}</span>}
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
      className={`flex-1 py-4 text-xl font-black tracking-wider rounded-lg ${mode === m ? on : "text-neutral-400"} disabled:opacity-60`}
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
    <section className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold">Demo panel</h2>
        <button type="button" onClick={onClose} className="text-neutral-400 px-2 py-1">
          Close
        </button>
      </div>

      <div className="flex gap-1 p-1 rounded-xl bg-neutral-900 border border-neutral-700">
        {seg("nightscout", "LIVE", "bg-emerald-500 text-black")}
        {seg("replay", "DEMO", "bg-amber-400 text-black")}
      </div>
      <p className="text-sm text-neutral-400">
        {mode === undefined
          ? "Waiting for your Irin…"
          : demo
            ? `Demo: replayed data, badged DEMO on every screen.${info?.current ? ` Playing ${info.current} at ${speed}×.` : ""}${
                info?.clock ? ` Replay clock ${clockText(info.clock)}.` : ""
              }`
            : "Live: real readings. Demo controls are off."}
      </p>
      {msg && (
        <p role="status" className={`text-sm ${msg.error ? "text-red-400" : "text-emerald-400"}`}>
          {msg.text}
        </p>
      )}

      <fieldset disabled={!demo || busy} className={`flex flex-col gap-2 ${demo ? "" : "opacity-40"}`}>
        <legend className="text-xs uppercase tracking-wider text-neutral-500 mb-1">
          {demo ? "Demo controls" : "Demo controls (demo mode only)"}
        </legend>

        <Control title="Scenario" hint="plays from its first reading">
          <div className="flex gap-2">
            <select
              aria-label="Scenario"
              className="flex-1 bg-neutral-900 border border-neutral-700 rounded-lg px-3 py-2 text-white"
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
              className={`${btn} bg-white text-black`}
              onClick={() => run(() => playScenario(baseUrl, picked), `Playing ${picked.replace(/_/g, " ")} from the start.`, () => {
                refresh();
              })}
            >
              Play
            </button>
          </div>
        </Control>

        <Control title="Replay speed" hint="clock-minutes per real minute">
          <div className="flex gap-2">
            {SPEEDS.map((s) => (
              <button
                key={s}
                type="button"
                aria-pressed={speed === s}
                className={`${btn} flex-1 ${speed === s ? "bg-white text-black" : "bg-neutral-900 text-neutral-300"}`}
                onClick={() => run(() => setSpeed(baseUrl, s), `Speed ${s}×.`, refresh)}
              >
                {s}×
              </button>
            ))}
          </div>
        </Control>

        <Control title="Sensor feed" hint="paused: the reading goes stale after 15 clock-min">
          <button
            type="button"
            aria-pressed={paused}
            className={`${btn} ${paused ? "bg-amber-400 text-black" : "bg-neutral-900 text-neutral-300"}`}
            onClick={() =>
              run(() => setPaused(baseUrl, !paused), paused ? "Feed resumed." : "Feed paused.", refresh)
            }
          >
            {paused ? "Resume feed" : "Pause feed"}
          </button>
        </Control>

        <Control title="Inject a low" hint="one reading now, falling; the scenario stays clean">
          <div className="flex gap-2">
            <input
              aria-label="Injected glucose (mg/dL)"
              className="w-24 bg-neutral-900 border border-neutral-700 rounded-lg px-3 py-2 text-white"
              type="number"
              inputMode="numeric"
              min={INJECT_MIN}
              max={INJECT_MAX}
              value={injectValue}
              onChange={(e) => setInjectValue(e.target.value)}
            />
            <span className="self-center text-neutral-400 text-sm">mg/dL</span>
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

        <Control title="Basal-time nudge" hint="basal time set to 61 min ago; restored on the switch to live">
          <button
            type="button"
            className={`${btn} bg-neutral-900 text-neutral-300`}
            onClick={() => run(() => basalNudge(baseUrl), "Basal nudge triggered.")}
          >
            Show the basal nudge
          </button>
        </Control>

        <Control title="Send a card to the doctor" hint="a sample card, badged DEMO, sealed to the paired demo doctor">
          <div className="flex gap-2">
            {([
              ["signal_card_standing", "Basal Check"],
              ["signal_card_step", "Step check"],
            ] as [SampleFixture, string][]).map(([f, label]) => (
              <button
                key={f}
                type="button"
                className={`${btn} flex-1 bg-neutral-900 text-neutral-200`}
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

        <Control title="Jump to step N day D, or to a date" hint="the step-2 check appears at step 2 day 8; clear the step for day D of the scenario; a date wins">
          <div className="flex gap-2 items-center">
            <span className="text-neutral-400 text-sm">step</span>
            <input
              aria-label="Plan step"
              className="w-16 bg-neutral-900 border border-neutral-700 rounded-lg px-2 py-2 text-white"
              type="number"
              inputMode="numeric"
              min={1}
              value={seekStep}
              onChange={(e) => setSeekStep(e.target.value)}
            />
            <span className="text-neutral-400 text-sm">day</span>
            <input
              aria-label="Day"
              className="w-16 bg-neutral-900 border border-neutral-700 rounded-lg px-2 py-2 text-white"
              type="number"
              inputMode="numeric"
              min={1}
              value={seekDay}
              onChange={(e) => setSeekDay(e.target.value)}
            />
            <button
              type="button"
              disabled={!seekOk}
              className={`${btn} flex-1 bg-white text-black`}
              onClick={() =>
                run(async () => {
                  const r = await seek(
                    baseUrl,
                    seekDate ? { date: seekDate } : stepN === null ? { day: dayN } : { step: stepN, day: dayN },
                  );
                  if (!r.ok) return r.reason;
                  setMsg({ text: seekText(r.body), error: false });
                  return "";
                }, "", refresh)
              }
            >
              Jump
            </button>
          </div>
          <div className="flex gap-2 items-center">
            <span className="text-neutral-400 text-sm">or date</span>
            <input
              aria-label="Seek date"
              className="flex-1 bg-neutral-900 border border-neutral-700 rounded-lg px-2 py-2 text-white"
              type="date"
              value={seekDate}
              onChange={(e) => setSeekDate(e.target.value)}
            />
            {seekDate && (
              <button type="button" className="text-sm text-neutral-400 underline" onClick={() => setSeekDate("")}>
                clear
              </button>
            )}
          </div>
          <span className="text-xs text-neutral-500">
            Lands at 09:00 of that day; the catch-up builds every night, question and card up to it. Forward only.
            {info?.companion ? "" : " This scenario has no companion (no plan to seek by step)."}
          </span>
        </Control>

        <Control title="Irin Brain only" hint="cards change confidence labels, never blank a row">
          <button
            type="button"
            aria-pressed={brainOnly}
            className={`${btn} ${brainOnly ? "bg-sky-300 text-black" : "bg-neutral-900 text-neutral-300"}`}
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
            {brainOnly ? "Brain only (tap for Bedside)" : "Bedside (tap for Brain only)"}
          </button>
        </Control>

        <Control title="Send the real card" hint="today's card from the engine: Brain only, or Bedside and Brain side by side">
          <div className="flex gap-2">
            {(
              [
                ["brain", "Brain-only"],
                ["both", "Bedside + Brain"],
              ] as const
            ).map(([m, label]) => (
              <button
                key={m}
                type="button"
                className={`${btn} flex-1 bg-neutral-900 text-neutral-200`}
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

        <Control title="Simulate a Spark offer" hint="Impiricus Spark (simulated) offers the scenario's plan">
          <button
            type="button"
            className={`${btn} bg-neutral-900 text-neutral-200`}
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
            Simulate Spark offer
          </button>
        </Control>

        <Control title="Trigger the buddy rung" hint="arrives with the Night Buddy tier">
          <button
            type="button"
            className={`${btn} bg-neutral-900 text-neutral-200`}
            onClick={() =>
              run(async () => {
                const r = await buddyRung(baseUrl);
                if (!r.ok) return r.reason;
                setMsg({ text: `Buddy rung: ${r.body.status}.`, error: false });
                return "";
              }, "")
            }
          >
            Trigger buddy rung
          </button>
        </Control>
      </fieldset>
    </section>
  );
}
