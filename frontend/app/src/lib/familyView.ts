import { useSyncExternalStore } from "react";
import { CLOUD_URL, FAMILY_URL } from "../config";
import { getPin } from "./usePin";

// The family VIEW link (justin.md step 9 / A3; cloud/README.md "Family"): a
// Level 2 ("Story + view") recipient gets a revocable family bearer from Irin
// Cloud, POST /v1/family/bearers (X-PIN) {recipient_id, demo}, and the link
// {FAMILY_URL}/#t=<token> (the fragment never reaches a server). The token is
// shown ONCE and never stored here; this phone keeps only the bearer_id, to
// turn the link off with DELETE /v1/family/bearers?bearer_id= (X-PIN). A Level
// 1 recipient never gets a link: that level promises no numbers.
const KEY = "irin.family.links";
const EVENT = "irin:family-links";

export interface ViewLink {
  bearer_id: string;
  is_demo: boolean;
  created_at: string;
}

function read(): Record<string, ViewLink> {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) || "{}");
    return v && typeof v === "object" ? v : {};
  } catch {
    return {};
  }
}
let snapshot = read();
function write(v: Record<string, ViewLink>) {
  try {
    localStorage.setItem(KEY, JSON.stringify(v));
  } catch {
    /* kept for this session only */
  }
  snapshot = v;
  window.dispatchEvent(new Event(EVENT));
}
export function useViewLinks(): Record<string, ViewLink> {
  return useSyncExternalStore(
    (cb) => {
      window.addEventListener(EVENT, cb);
      return () => window.removeEventListener(EVENT, cb);
    },
    () => snapshot,
  );
}

async function reason(res: Response): Promise<string> {
  if (res.status === 401) return "Irin Cloud did not accept your PIN.";
  if (res.status === 503) return "Irin Cloud cannot issue family links right now.";
  const b = (await res.json().catch(() => null)) as { detail?: unknown } | null;
  return typeof b?.detail === "string" ? `Irin Cloud refused it: ${b.detail}` : `Irin Cloud refused it (${res.status}).`;
}

export async function createViewLink(recipientId: string, demo: boolean): Promise<{ ok: true; url: string } | { ok: false; reason: string }> {
  let res: Response;
  try {
    res = await fetch(`${CLOUD_URL}/v1/family/bearers`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-PIN": getPin() ?? "" },
      body: JSON.stringify({ recipient_id: recipientId, demo }),
    });
  } catch {
    return { ok: false, reason: "Cannot reach Irin Cloud. No link was made." };
  }
  if (!res.ok) return { ok: false, reason: await reason(res) };
  const b = (await res.json()) as { bearer_id: string; token: string; is_demo: boolean; created_at: string };
  write({ ...snapshot, [recipientId]: { bearer_id: b.bearer_id, is_demo: b.is_demo, created_at: b.created_at } });
  return { ok: true, url: `${FAMILY_URL.replace(/\/$/, "")}/#t=${encodeURIComponent(b.token)}` };
}

/** Turn a recipient's link off. A link Irin Cloud no longer knows (404) is off already. */
export async function revokeViewLink(recipientId: string): Promise<{ ok: true } | { ok: false; reason: string }> {
  const link = snapshot[recipientId];
  if (!link) return { ok: true };
  let res: Response;
  try {
    res = await fetch(`${CLOUD_URL}/v1/family/bearers?bearer_id=${encodeURIComponent(link.bearer_id)}`, {
      method: "DELETE",
      headers: { "X-PIN": getPin() ?? "" },
    });
  } catch {
    return { ok: false, reason: "Cannot reach Irin Cloud: the view link is still on. Try again." };
  }
  if (!res.ok && res.status !== 404) return { ok: false, reason: await reason(res) };
  const rest = { ...snapshot };
  delete rest[recipientId];
  write(rest);
  return { ok: true };
}
