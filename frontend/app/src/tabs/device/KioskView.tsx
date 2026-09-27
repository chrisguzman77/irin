import { useCallback, useEffect, useState } from "react";
import { deviceFetch, PinRejected } from "../../lib/api";

// Which screen the kiosk shows: Auto follows the night window, Live pins the
// detail view, Night pins the dim night view. The Pi's route returns plain
// JSON (no response model yet), read defensively: `mode` is the choice,
// `effective` what the kiosk is showing now.
type Mode = "auto" | "detail" | "night";
const MODES: [Mode, string][] = [["auto", "Auto"], ["detail", "Live"], ["night", "Night"]];
const SHOWING: Record<string, string> = { detail: "Live", night: "Night", morning: "Morning" };

export default function KioskView({ baseUrl }: { baseUrl: string }) {
  const [mode, setMode] = useState<Mode | null>(null);
  const [effective, setEffective] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const read = useCallback(async () => {
    try {
      const res = await deviceFetch(baseUrl, "/api/display_mode");
      if (!res.ok) throw new Error(res.status === 404 ? "Your Irin does not have this switch yet." : `${res.status}`);
      const b = (await res.json()) as { mode?: unknown; effective?: unknown };
      setMode(MODES.some(([m]) => m === b.mode) ? (b.mode as Mode) : null);
      setEffective(typeof b.effective === "string" ? b.effective : null);
      setErr("");
    } catch (e) {
      if (e instanceof PinRejected) return;
      setErr(e instanceof Error && e.message.startsWith("Your Irin") ? e.message : "Could not read the kiosk view.");
    }
  }, [baseUrl]);
  useEffect(() => {
    read();
  }, [read]);

  const choose = async (m: Mode) => {
    if (busy || m === mode) return;
    setBusy(true);
    setErr("");
    try {
      const res = await deviceFetch(baseUrl, "/api/display_mode", { method: "POST", body: JSON.stringify({ mode: m }) });
      if (!res.ok) throw new Error();
      await read();
    } catch (e) {
      if (!(e instanceof PinRejected)) setErr("Could not change the kiosk view. Nothing changed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <fieldset className="border border-neutral-800 rounded-xl px-4 py-2 mb-4">
      <legend className="px-1 text-neutral-400 text-xs uppercase tracking-wider">Kiosk view</legend>
      <div role="radiogroup" aria-label="Kiosk view" className="flex rounded-lg border border-neutral-700 overflow-hidden my-2">
        {MODES.map(([m, label]) => (
          <button
            key={m}
            type="button"
            role="radio"
            aria-checked={mode === m}
            disabled={busy}
            onClick={() => choose(m)}
            className={`flex-1 py-2 text-sm disabled:opacity-60 ${mode === m ? "bg-amber-400 text-black font-semibold" : "text-neutral-300"}`}
          >
            {label}
          </button>
        ))}
      </div>
      {effective && <p className="text-sm text-neutral-300">Showing now: {SHOWING[effective] ?? effective}</p>}
      {err && <p role="status" className="text-sm text-red-400">{err}</p>}
      <p className="text-xs text-neutral-500 pb-2">Alarms always take over the screen, whatever the view.</p>
    </fieldset>
  );
}
