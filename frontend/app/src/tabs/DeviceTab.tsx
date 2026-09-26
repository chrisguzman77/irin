import { useState } from "react";
import { useDevice } from "../lib/device";
import AckBar from "./device/AckBar";
import LiveView from "./device/LiveView";
import SettingsForm from "./device/SettingsForm";

// The Device tab. A2 (pairing) is not built yet: without the development
// override it shows the pairing entry point. The acknowledge bar sits above
// every Device screen: an alarm outranks whatever the user was doing.
const VIEWS = ["Live", "Settings"] as const;
type View = (typeof VIEWS)[number];

export default function DeviceTab() {
  const { target, socket } = useDevice();
  const [view, setView] = useState<View>("Live");

  if (target.status === "resolving") return <p className="text-neutral-400">Looking for your Irin…</p>;
  if (target.status === "unpaired") {
    return (
      <section className="flex flex-col gap-4">
        <h2 className="text-xl font-semibold">Irin Device</h2>
        <button className="bg-amber-400 text-black px-4 py-3 rounded-lg text-lg font-semibold" disabled>
          Pair your Irin
        </button>
        <p className="text-neutral-400 text-sm">Pairing arrives with step A2.</p>
      </section>
    );
  }
  const snap = socket.snapshot;
  const a = snap?.alarm;
  return (
    <>
      {/* keyed per alarm episode/state so a previous tap's message never carries over */}
      <AckBar key={`${a?.trigger_type}-${a?.state}-${a?.started_at}`} alarm={a} baseUrl={target.url} />
      <div className="flex gap-2 mb-2">
        {VIEWS.map((v) => (
          <button
            key={v}
            type="button"
            onClick={() => setView(v)}
            className={`px-3 py-1.5 rounded-full text-sm ${
              v === view ? "bg-white text-black font-semibold" : "bg-neutral-900 text-neutral-300"
            }`}
          >
            {v}
          </button>
        ))}
      </div>
      {view === "Live" && <LiveView snap={snap} stale={socket.disconnectedLong} />}
      {view === "Settings" && <SettingsForm current={snap?.settings} baseUrl={target.url} />}
    </>
  );
}
