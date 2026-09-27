import { useEffect, useState } from "react";
import { DEVICE_URL_OVERRIDE } from "../config";
import { useOwnerPairing } from "./owner";

export type DeviceTarget =
  | { status: "resolving" }
  | { status: "unpaired" }
  | { status: "ready"; url: string; dev: boolean };

/** Where the Device tab talks to. Development: VITE_DEVICE_URL, used only
 * when that backend's /api/health reports hw: mock (a real HAL ignores it) --
 * it always wins while it does. Production (A2): the paired device_url from
 * lib/owner.ts, or unpaired when there is none. */
export function useDeviceTarget(): DeviceTarget {
  const owner = useOwnerPairing();
  const [override, setOverride] = useState<DeviceTarget | null>(DEVICE_URL_OVERRIDE ? { status: "resolving" } : null);
  useEffect(() => {
    if (!DEVICE_URL_OVERRIDE) return;
    const url = DEVICE_URL_OVERRIDE.replace(/\/$/, "");
    let alive = true;
    const probe = async () => {
      try {
        const health = await (await fetch(`${url}/api/health`)).json();
        if (alive) setOverride(health.hw === "mock" ? { status: "ready", url, dev: true } : null);
      } catch {
        if (alive) setTimeout(probe, 3000); // backend not up yet; keep trying
      }
    };
    probe();
    return () => {
      alive = false;
    };
  }, []);
  if (override) return override;
  return owner ? { status: "ready", url: owner.device_url, dev: false } : { status: "unpaired" };
}
