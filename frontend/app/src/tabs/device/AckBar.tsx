import { useState } from "react";
import type { AlarmState } from "../../lib/contracts";
import { sendAcknowledge, soundingLow } from "../../lib/alarm";

// Step 4: full width at the top, visible only while a low alarm sounds.
// It disappears when the Pi says the alarm changed, not when tapped.
export default function AckBar({ alarm, baseUrl }: { alarm: AlarmState | undefined; baseUrl: string }) {
  const tier = soundingLow(alarm);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  if (!tier) return null;
  const full = tier === "full";
  return (
    <div
      role="alert"
      className={`-mx-4 -mt-4 mb-4 px-4 py-4 ${full ? "bg-red-600 text-white" : "bg-amber-400 text-black"}`}
    >
      <div className="text-center font-black tracking-wider text-lg">
        {full ? (alarm?.state === "rearmed" ? "STILL LOW — TREAT NOW" : "LOW — TREAT NOW") : "LOW COMING — predicted within 30 min"}
      </div>
      <button
        type="button"
        disabled={busy}
        className={`mt-3 w-full py-5 rounded-xl text-2xl font-black ${
          full ? "bg-white text-red-700" : "bg-black text-amber-400"
        } active:scale-[0.98] disabled:opacity-60`}
        onClick={async () => {
          setBusy(true);
          setMsg("");
          const res = await sendAcknowledge(baseUrl);
          setBusy(false);
          if (!res.ok) setMsg(res.reason ?? "Acknowledge failed.");
        }}
      >
        Acknowledge
      </button>
      {msg && <p className="mt-2 text-center text-sm font-semibold">{msg}</p>}
    </div>
  );
}
