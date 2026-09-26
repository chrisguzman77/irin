import { useState } from "react";
import type { components } from "../../types/pi";
import { deviceFetch, PinRejected } from "../../lib/api";
import type { StateSnapshot } from "../../lib/contracts";

// Step 8: the demo panel, stagecraft reached from a small button, never a
// main tab. The LIVE/DEMO switch drives POST /api/mode (PIN-gated); the
// screen changes only when the Pi's mode_change arrives. Everything below
// the switch works only in demo mode and is greyed in live mode.
type ModeRequest = components["schemas"]["ModeRequest"];

// Controls whose backend endpoints do not exist yet (chris.md step 12 and
// the sponsor tiers). Listed so the panel's shape is settled; each gets
// wired when its endpoint lands.
const PENDING_CONTROLS = ["Scenario", "Replay speed", "Pause feed", "Inject low", "Basal-time nudge"];

export default function DemoPanel({ snap, baseUrl, onClose }: { snap: StateSnapshot | null; baseUrl: string; onClose: () => void }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const mode = snap?.mode;
  const demo = mode === "replay";

  const switchTo = async (m: ModeRequest["mode"]) => {
    if (m === mode || busy) return;
    setBusy(true);
    setMsg("");
    try {
      const body: ModeRequest = { mode: m };
      const res = await deviceFetch(baseUrl, "/api/mode", { method: "POST", body: JSON.stringify(body) });
      if (!res.ok) setMsg(`Your Irin refused the switch (${res.status}).`);
    } catch (e) {
      setMsg(e instanceof PinRejected ? "" : "Could not reach your Irin.");
    } finally {
      setBusy(false);
    }
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
            ? "Demo: replayed data, badged DEMO on every screen."
            : "Live: real readings. Demo controls are off."}
        {busy && " Switching…"}
      </p>
      {msg && <p className="text-red-400 text-sm">{msg}</p>}

      <fieldset disabled className={`flex flex-col gap-2 ${demo ? "" : "opacity-40"}`}>
        <legend className="text-xs uppercase tracking-wider text-neutral-500 mb-1">
          {demo ? "Demo controls" : "Demo controls (demo mode only)"}
        </legend>
        {PENDING_CONTROLS.map((c) => (
          <div key={c} className="flex items-center justify-between rounded-lg border border-neutral-800 px-3 py-3">
            <span className="text-neutral-300">{c}</span>
            <span className="text-xs text-neutral-500">not built on the device yet</span>
          </div>
        ))}
      </fieldset>
    </section>
  );
}
