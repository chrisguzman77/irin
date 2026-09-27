import { useEffect, useState } from "react";
import DoctorTakeover from "./components/DoctorTakeover";
import FreshPinPrompt from "./components/FreshPinPrompt";
import Gate from "./components/Gate";
import StatusBar from "./components/StatusBar";
import { DeviceProvider } from "./lib/device";
import { setPendingPairCode } from "./lib/owner";
import { usePin } from "./lib/usePin";
import DeviceTab from "./tabs/DeviceTab";
import MyIrinTab from "./tabs/MyIrinTab";
import BuddyTab from "./tabs/BuddyTab";
import RoundsTab from "./tabs/RoundsTab";
import logo from "./assets/irin-logo.svg";

// A1: the gate and the shell. The code lives in sessionStorage (usePin) and is
// sent as X-PIN on every mutating call (lib/api.ts); the active tab is
// remembered in localStorage; every screen renders from the device snapshot
// through the one useDeviceSocket (lib/device.tsx).
const TAB_KEY = "irin.tab";
const TABS = ["Irin Device", "My Irin", "Irin Buddy", "Irin Rounds"] as const;
type Tab = (typeof TABS)[number];
const HASH_CODE_RE = /^#pair=(\d{6})$/;

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

  const select = (t: Tab) => {
    try {
      localStorage.setItem(TAB_KEY, t);
    } catch {
      /* the tab just isn't remembered */
    }
    setTab(t);
  };

  // A2: a QR's #pair=NNNNNN can land on whichever tab was last remembered,
  // not necessarily Device. Take the code once, wipe it from the URL, and
  // switch to Device so the pairing form gets it (lib/owner.ts).
  useEffect(() => {
    const m = HASH_CODE_RE.exec(location.hash);
    if (!m) return;
    setPendingPairCode(m[1]);
    history.replaceState(null, "", location.pathname + location.search);
    select("Irin Device");
  }, []);

  if (!pin) return <Gate />;

  return (
    <DeviceProvider>
      <div className="app-shell min-h-dvh bg-irin-ink text-irin-cream">
        <StatusBar />
        <DoctorTakeover />
        <FreshPinPrompt />
        <header className="flex items-center px-4 pt-3 pb-2">
          <img src={logo} alt="Irin" className="h-7 w-auto select-none" draggable={false} />
        </header>
        <nav className="relative flex border-b border-irin-line bg-irin-ink">
          {TABS.map((t) => (
            <button
              key={t}
              className={`flex-1 py-3 text-sm sm:text-base transition-colors duration-200 ${
                t === tab ? "text-irin-cream font-semibold" : "text-irin-sage hover:text-irin-cream"
              }`}
              onClick={() => select(t)}
            >
              {t}
            </button>
          ))}
          {/* the active-tab underline slides between the four equal-width tabs */}
          <span
            aria-hidden="true"
            className="tab-indicator absolute bottom-0 left-0 h-0.5 rounded-full bg-[#B1D2BD]"
            style={{ width: `${100 / TABS.length}%`, transform: `translateX(${TABS.indexOf(tab) * 100}%)` }}
          />
        </nav>
        <main key={tab} className={`tab-enter p-4 mx-auto ${tab === "Irin Rounds" ? "max-w-6xl" : "max-w-2xl"}`}>
          {tab === "Irin Device" && <DeviceTab />}
          {tab === "My Irin" && <MyIrinTab />}
          {tab === "Irin Buddy" && <BuddyTab />}
          {tab === "Irin Rounds" && <RoundsTab />}
        </main>
      </div>
    </DeviceProvider>
  );
}
