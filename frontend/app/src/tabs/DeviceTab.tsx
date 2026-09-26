import { useEffect, useState } from "react";
import { useDevice } from "../lib/device";
import AckBar from "./device/AckBar";
import DemoPanel from "./device/DemoPanel";
import LiveView from "./device/LiveView";
import SettingsForm from "./device/SettingsForm";

// The Device tab. A2 (pairing) is not built yet: without the development
// override it shows the pairing entry point. The acknowledge bar sits above
// every Device screen: an alarm outranks whatever the user was doing.
const VIEWS = ["Live", "Settings"] as const;
type View = (typeof VIEWS)[number] | "Demo";

// The demo panel is an in-app route (/demo) reached only by its button;
// loading /demo directly just opens the app, and Back closes the panel.
const DEMO_PATH = "/demo";

export default function DeviceTab() {
  const { target, socket } = useDevice();
  const [view, setView] = useState<View>("Live");

  useEffect(() => {
    if (location.pathname === DEMO_PATH) history.replaceState(null, "", "/");
    const onPop = () => setView((v) => (v === "Demo" && location.pathname !== DEMO_PATH ? "Live" : v));
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  const openDemo = () => {
    history.pushState(null, "", DEMO_PATH);
    setView("Demo");
  };
  const closeDemo = () => {
    if (location.pathname === DEMO_PATH) history.back();
    setView("Live");
  };

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
            onClick={() => {
              if (location.pathname === DEMO_PATH) history.replaceState(null, "", "/");
              setView(v);
            }}
            className={`px-3 py-1.5 rounded-full text-sm ${
              v === view ? "bg-white text-black font-semibold" : "bg-neutral-900 text-neutral-300"
            }`}
          >
            {v}
          </button>
        ))}
        <button
          type="button"
          onClick={openDemo}
          aria-label="Demo panel"
          title="Demo panel"
          className={`ml-auto px-3 py-1.5 rounded-full text-xs ${view === "Demo" ? "bg-amber-400 text-black" : "text-neutral-500 border border-neutral-800"}`}
        >
          demo
        </button>
      </div>
      {view === "Live" && <LiveView snap={snap} stale={socket.disconnectedLong} />}
      {view === "Settings" && <SettingsForm current={snap?.settings} baseUrl={target.url} />}
      {view === "Demo" && <DemoPanel snap={snap} baseUrl={target.url} onClose={closeDemo} />}
    </>
  );
}
