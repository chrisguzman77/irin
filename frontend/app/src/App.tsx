import { useState } from "react";
import DoctorTakeover from "./components/DoctorTakeover";
import FreshPinPrompt from "./components/FreshPinPrompt";
import Gate from "./components/Gate";
import StatusBar from "./components/StatusBar";
import { DeviceProvider } from "./lib/device";
import { usePin } from "./lib/usePin";
import DeviceTab from "./tabs/DeviceTab";
import MyIrinTab from "./tabs/MyIrinTab";
import BuddyTab from "./tabs/BuddyTab";
import RoundsTab from "./tabs/RoundsTab";

// A1: the gate and the shell. The code lives in sessionStorage (usePin) and is
// sent as X-PIN on every mutating call (lib/api.ts); the active tab is
// remembered in localStorage; every screen renders from the device snapshot
// through the one useDeviceSocket (lib/device.tsx).
const TAB_KEY = "irin.tab";
const TABS = ["Irin Device", "My Irin", "Irin Buddy", "Irin Rounds"] as const;
type Tab = (typeof TABS)[number];

function storedTab(): Tab {
  try {
    const t = localStorage.getItem(TAB_KEY);
    return (TABS as readonly string[]).includes(t ?? "") ? (t as Tab) : "Irin Device";
  } catch {
    return "Irin Device";
  }
}

export default function App() {
  const pin = usePin();
  const [tab, setTab] = useState<Tab>(storedTab);

  if (!pin) return <Gate />;

  const select = (t: Tab) => {
    try {
      localStorage.setItem(TAB_KEY, t);
    } catch {
      /* the tab just isn't remembered */
    }
    setTab(t);
  };

  return (
    <DeviceProvider>
      <div className="min-h-dvh bg-irin-ink text-irin-cream">
        <StatusBar />
        <DoctorTakeover />
        <FreshPinPrompt />
        <nav className="flex border-b border-irin-line bg-irin-ink">
          {TABS.map((t) => (
            <button
              key={t}
              className={`flex-1 py-3 text-sm sm:text-base ${
                t === tab ? "border-b-2 border-irin-mint text-irin-cream font-semibold" : "text-irin-sage"
              }`}
              onClick={() => select(t)}
            >
              {t}
            </button>
          ))}
        </nav>
        <main className="p-4 max-w-2xl mx-auto">
          {tab === "Irin Device" && <DeviceTab />}
          {tab === "My Irin" && <MyIrinTab />}
          {tab === "Irin Buddy" && <BuddyTab />}
          {tab === "Irin Rounds" && <RoundsTab />}
        </main>
      </div>
    </DeviceProvider>
  );
}
