import { useEffect, useState } from "react";
import { useBasalNudge } from "../lib/useBasalNudge";
import { useDevice } from "../lib/device";
import { getAccount, linkAccount } from "../lib/account";
import { getOwnerPairing, pairOwnerDevice, takePendingPairCode } from "../lib/owner";
import TreatingButton from "./buddy/TreatingButton";
import AckBar from "./device/AckBar";
import DemoPanel from "./device/DemoPanel";
import DisconnectDevice from "./device/DisconnectDevice";
import LiveView from "./device/LiveView";
import LogView from "./device/LogView";
import RecallCards from "./device/RecallCards";
import WatchToday from "./rounds/WatchToday";
import ReportsView from "./device/ReportsView";
import SettingsForm from "./device/SettingsForm";

// The Device tab. Unpaired (A2): a 6-digit code (typed, or prefilled from a
// #pair= hash the kiosk's QR encodes) pairs this phone through the relay.
// The acknowledge bar sits above every Device screen: an alarm outranks
// whatever the user was doing.
const VIEWS = ["Live", "Log", "Reports", "Settings"] as const;
type View = (typeof VIEWS)[number] | "Demo";

// The demo panel is an in-app route (/demo) reached only by its button;
// loading /demo directly just opens the app, and Back closes the panel.
const DEMO_PATH = "/demo";

export default function DeviceTab() {
  const { target, socket } = useDevice();
  const [view, setView] = useState<View>("Live");
  const nudge = useBasalNudge(target.status === "ready" ? target.url : null);

  const [code, setCode] = useState("");
  const [name, setName] = useState("My phone");
  const [pairing, setPairing] = useState(false);
  const [pairMsg, setPairMsg] = useState("");
  // Phone-only accounts (Phase 1): what linking the buddy account to this
  // pairing said, shown once on the paired screen (the form is gone by then).
  const [linkNote, setLinkNote] = useState("");

  // App.tsx already read a QR's #pair=NNNNNN (it can land on any remembered
  // tab) and switched here; take the code the one time this mounts after.
  useEffect(() => {
    const pending = takePendingPairCode();
    if (pending) setCode(pending);
  }, []);

  useEffect(() => {
    if (location.pathname === DEMO_PATH) history.replaceState(null, "", "/");
    const onPop = () => setView((v) => (v === "Demo" && location.pathname !== DEMO_PATH ? "Live" : v));
    window.addEventListener("popstate", onPop);
    return () => {
      window.removeEventListener("popstate", onPop);
      // leaving the Device tab closes the panel, so /demo never outlives it
      if (location.pathname === DEMO_PATH) history.replaceState(null, "", "/");
    };
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
    const codeValid = /^\d{6}$/.test(code);
    const nameValid = name.trim().length > 0 && name.trim().length <= 40;
    return (
      <section className="flex flex-col gap-4">
        <h2 className="text-xl font-semibold">Irin Device</h2>
        <form
          className="flex flex-col gap-3"
          onSubmit={async (e) => {
            e.preventDefault();
            if (pairing || !codeValid || !nameValid) return;
            setPairing(true);
            setPairMsg("");
            const res = await pairOwnerDevice(code, name.trim());
            if (!res.ok) {
              setPairing(false);
              return setPairMsg(res.reason);
            }
            // a buddy account signed up on this phone joins the Irin's directory
            // document (relay POST /v0/users/link); a 409 is shown and both stay as they are
            const owner = getOwnerPairing();
            if (getAccount() && owner) {
              const link = await linkAccount(owner.device_id, owner.token);
              setLinkNote(link.ok ? "Your buddy account is now linked to this Irin." : link.reason);
            }
            setPairing(false);
          }}
        >
          <label className="flex flex-col gap-1">
            <span className="text-sm text-neutral-300">6-digit code</span>
            <input
              className="w-40 text-center text-2xl tracking-[0.3em] p-3 rounded-lg bg-neutral-900 border border-neutral-700 text-white"
              inputMode="numeric"
              autoComplete="off"
              maxLength={6}
              value={code}
              onChange={(e) => {
                setCode(e.target.value.replace(/\D/g, "").slice(0, 6));
                setPairMsg("");
              }}
              autoFocus
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-sm text-neutral-300">Name this phone</span>
            <input
              className="bg-neutral-900 border border-neutral-700 rounded-lg px-3 py-2 text-white w-full"
              autoComplete="off"
              maxLength={40}
              value={name}
              onChange={(e) => setName(e.target.value.slice(0, 40))}
            />
          </label>
          <button
            type="submit"
            disabled={pairing || !codeValid || !nameValid}
            className="bg-amber-400 text-black px-4 py-3 rounded-lg text-lg font-semibold disabled:opacity-40"
          >
            {pairing ? "Pairing…" : "Pair"}
          </button>
          {pairMsg && <p className="text-red-400 text-sm">{pairMsg}</p>}
        </form>
        <p className="text-neutral-400 text-sm">
          On your Irin: Settings → Pair a phone. Stand in front of it; it shows a QR and a 6-digit code.
        </p>
      </section>
    );
  }
  const snap = socket.snapshot;
  const a = snap?.alarm;
  return (
    <>
      {/* keyed per alarm episode/state so a previous tap's message never carries over */}
      <AckBar key={`${a?.trigger_type}-${a?.state}-${a?.started_at}`} alarm={a} baseUrl={target.url} />
      <TreatingButton snap={snap} baseUrl={target.url} />
      {linkNote && (
        <p role="status" className="mb-4 text-sm text-amber-300 flex items-start gap-2">
          <span className="flex-1">{linkNote}</span>
          <button type="button" aria-label="Dismiss" className="text-neutral-400" onClick={() => setLinkNote("")}>×</button>
        </p>
      )}
      <div className="flex gap-2 mb-4">
        {VIEWS.map((v) => (
          <button
            key={v}
            type="button"
            onClick={() => {
              if (location.pathname === DEMO_PATH) history.replaceState(null, "", "/");
              setView(v);
            }}
            className={`px-3 py-1.5 rounded-full text-sm ${
              v === view ? "bg-irin-mint text-irin-ink font-semibold" : "bg-irin-surface text-irin-sage"
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
          className={`ml-auto px-3 py-1.5 rounded-full text-xs ${view === "Demo" ? "bg-amber-400 text-black" : "text-irin-sage border border-irin-line"}`}
        >
          demo
        </button>
      </div>
      {view === "Live" && (
        <RecallCards
          items={socket.recallDue}
          baseUrl={target.url}
          demo={snap?.mode === "replay"}
        />
      )}
      {view === "Live" && <LiveView snap={snap} stale={socket.disconnectedLong} basalNudge={nudge !== "none"} onLog={() => setView("Log")} baseUrl={target.url} />}
      {view === "Live" && snap && (
        <div className="mt-4">
          <WatchToday snap={snap} baseUrl={target.url} planState={socket.planState} />
        </div>
      )}
      {view === "Log" && <LogView baseUrl={target.url} settings={snap?.settings} />}
      {view === "Reports" && <ReportsView baseUrl={target.url} mode={snap?.mode} />}
      {/* Disconnect sits above the form: at the bottom the fixed Undo/Save bar covered it */}
      {view === "Settings" && !target.dev && <DisconnectDevice />}
      {view === "Settings" && <SettingsForm current={snap?.settings} baseUrl={target.url} />}
      {view === "Demo" && <DemoPanel snap={snap} baseUrl={target.url} onClose={closeDemo} />}
    </>
  );
}
