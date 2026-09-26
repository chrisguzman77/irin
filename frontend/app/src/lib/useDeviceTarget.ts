import { useEffect, useState } from "react";
import { DEVICE_URL_OVERRIDE } from "../config";

export type DeviceTarget =
  | { status: "resolving" }
  | { status: "unpaired" }
  | { status: "ready"; url: string; dev: boolean };

/** Where the Device tab talks to. Development: VITE_DEVICE_URL, used only
 * when that backend's /api/health reports hw: mock (a real HAL ignores it).
 * Production: the paired device_url from A2 (not built yet -> unpaired). */
export function useDeviceTarget(): DeviceTarget {
  const [target, setTarget] = useState<DeviceTarget>(
    DEVICE_URL_OVERRIDE ? { status: "resolving" } : { status: "unpaired" },
  );
  useEffect(() => {
    if (!DEVICE_URL_OVERRIDE) return;
    const url = DEVICE_URL_OVERRIDE.replace(/\/$/, "");
    let alive = true;
    const probe = async () => {
      try {
        const health = await (await fetch(`${url}/api/health`)).json();
        if (alive) setTarget(health.hw === "mock" ? { status: "ready", url, dev: true } : { status: "unpaired" });
      } catch {
        if (alive) setTimeout(probe, 3000); // backend not up yet; keep trying
      }
    };
    probe();
    return () => {
      alive = false;
    };
  }, []);
  return target;
}
