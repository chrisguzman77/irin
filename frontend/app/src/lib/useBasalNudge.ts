import { useEffect, useState } from "react";
import { deviceFetch } from "./api";

// The basal nudge (backend scheduler, chris.md step 10): "visual" at 60 min
// past the usual basal time with no basal logged, "email" at 90. It is not in
// the snapshot or any WS message yet (journal request), so it is read from
// GET /api/scheduler, polled like the kiosk does, only while the page is
// visible. A failed poll keeps the last known level.
export type NudgeLevel = "none" | "visual" | "email";

const POLL_MS = 5000;

export function useBasalNudge(baseUrl: string | null): NudgeLevel {
  const [level, setLevel] = useState<NudgeLevel>("none");

  useEffect(() => {
    if (!baseUrl) return;
    let cancelled = false;
    const poll = async () => {
      if (document.visibilityState === "hidden") return;
      try {
        const res = await deviceFetch(baseUrl, `/api/scheduler`, { cache: "no-store" });
        if (!res.ok) return;
        const b = (await res.json()) as { basal_nudge?: { level?: unknown } };
        const l = b.basal_nudge?.level;
        if (!cancelled) setLevel(l === "visual" || l === "email" ? l : "none");
      } catch {
        /* the disconnected banner covers a dead Pi */
      }
    };
    poll();
    const id = window.setInterval(poll, POLL_MS);
    document.addEventListener("visibilitychange", poll);
    return () => {
      cancelled = true;
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", poll);
    };
  }, [baseUrl]);

  return level;
}
