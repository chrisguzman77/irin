import { useState } from "react";
import DeviceTab from "./tabs/DeviceTab";
import MyIrinTab from "./tabs/MyIrinTab";
import BuddyTab from "./tabs/BuddyTab";
import RoundsTab from "./tabs/RoundsTab";

// The code gate (justin.md A1): the PIN lives in sessionStorage and is sent as the
// X-PIN header on every mutating call; never in a URL. Fresh-PIN verbs re-prompt
// (the list comes from GET /api/contracts/fresh_pin, never a hand copy).
const PIN_KEY = "irin.pin";
const TAB_KEY = "irin.tab";
const TABS = ["Irin Device", "My Irin", "Irin Buddy", "Irin Rounds"] as const;
type Tab = (typeof TABS)[number];

export function pinHeaders(): HeadersInit {
  const pin = sessionStorage.getItem(PIN_KEY) ?? "";
  return { "X-PIN": pin, "Content-Type": "application/json" };
}

export default function App() {
  const [pin, setPin] = useState<string>(() => sessionStorage.getItem(PIN_KEY) ?? "");
  const [tab, setTab] = useState<Tab>(() => (localStorage.getItem(TAB_KEY) as Tab) || "Irin Device");
  const [draft, setDraft] = useState("");

  if (!pin) {
    return (
      <form
        className="min-h-screen flex flex-col items-center justify-center gap-4 bg-black text-white"
        onSubmit={(e) => {
          e.preventDefault();
          sessionStorage.setItem(PIN_KEY, draft);
          setPin(draft);
        }}
      >
        <label className="text-lg">Enter your Irin code</label>
        <input
          className="text-black text-2xl p-2 rounded"
          type="password"
          inputMode="numeric"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          autoFocus
        />
        <button className="bg-amber-400 text-black px-4 py-2 rounded" type="submit">
          Open
        </button>
      </form>
    );
  }

  const select = (t: Tab) => {
    localStorage.setItem(TAB_KEY, t);
    setTab(t);
  };

  return (
    <div className="min-h-screen bg-black text-white">
      <nav className="flex border-b border-neutral-800">
        {TABS.map((t) => (
          <button
            key={t}
            className={`flex-1 py-3 ${t === tab ? "border-b-2 border-amber-400" : "text-neutral-400"}`}
            onClick={() => select(t)}
          >
            {t}
          </button>
        ))}
      </nav>
      <main className="p-4">
        {tab === "Irin Device" && <DeviceTab />}
        {tab === "My Irin" && <MyIrinTab />}
        {tab === "Irin Buddy" && <BuddyTab />}
        {tab === "Irin Rounds" && <RoundsTab />}
      </main>
    </div>
  );
}
