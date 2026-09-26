import { useCallback, useEffect, useState, type ReactNode } from "react";
import type { components } from "../../types/pi";
import { deviceFetch, PinRejected } from "../../lib/api";
import type { StateSnapshot } from "../../lib/contracts";
import {
  basalNudge, injectLow, listScenarios, playScenario, setPaused, setSpeed, type ScenarioList,
} from "../../lib/demo";

// Step 8: the demo panel, stagecraft reached from a small button, never a
// main tab. The LIVE/DEMO switch drives POST /api/mode (PIN-gated); the
// screen changes only when the Pi's mode_change arrives. Everything below
// the switch works only in demo mode (the Pi 404s it in live) and is greyed
// in live mode.
type ModeRequest = components["schemas"]["ModeRequest"];

const SPEEDS = [1, 10, 60, 120] as const;
const INJECT_MIN = 39;
const INJECT_MAX = 401;

// Sponsor controls that join the panel with their tiers (jump to step N day
// D, send card, brain_only, Spark offer, start watch, buddy rung).
const LATER_CONTROLS = ["Rounds and Night Buddy controls"];

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
  // The Pi does not report the pause state, so it is tracked here from our own
  // taps and reset whenever a scenario (re)starts or the mode changes.
  const [paused, setPausedState] = useState(false);
  const [injectValue, setInjectValue] = useState("55");
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
    setPausedState(false);
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
        setMsg({ text: done, error: false });
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
            ? `Demo: replayed data, badged DEMO on every screen.${info?.current ? ` Playing ${info.current} at ${speed}×.` : ""}`
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
                setPausedState(false);
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
              run(() => setPaused(baseUrl, !paused), paused ? "Feed resumed." : "Feed paused.", () => setPausedState(!paused))
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

        {LATER_CONTROLS.map((c) => (
          <div key={c} className="flex items-center justify-between rounded-lg border border-neutral-800 px-3 py-3">
            <span className="text-neutral-500">{c}</span>
            <span className="text-xs text-neutral-600">arrive with their tiers</span>
          </div>
        ))}
      </fieldset>
    </section>
  );
}
