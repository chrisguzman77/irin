import { useSyncExternalStore } from "react";
import { RELAY_URL } from "../config";

// Owner pairing (A2, relay/README.md "Owner pairing"): this phone pairs to
// exactly one Irin via a 6-digit code the kiosk shows. The relay hands back
// {device_id, device_url, token}; kept in localStorage (survives a closed
// tab, unlike the session PIN) so the phone stays paired across visits. The
// token travels only through this store and the Authorization header
// (lib/api.ts, lib/freshPin.ts), never in a URL (invariant 22).
const KEY = "irin.owner";
const EVENT = "irin:owner";

export interface OwnerPairing {
  device_id: string;
  device_url: string;
  token: string;
}

// Minimal local shape for POST /v0/device/pair's success body (relay/README.md
// "Owner pairing", A2). Not added to types/contracts.d.ts: that file is
// regenerated from the relay's live /openapi.json, and this route is still a
// 501 stub there while the relay side is being built in parallel.
type DevicePairResponse = OwnerPairing;

function read(): OwnerPairing | null {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const p = JSON.parse(raw) as Partial<OwnerPairing>;
    if (typeof p.device_id === "string" && typeof p.device_url === "string" && typeof p.token === "string") {
      return { device_id: p.device_id, device_url: p.device_url, token: p.token };
    }
    return null;
  } catch {
    return null;
  }
}

export function getOwnerPairing(): OwnerPairing | null {
  return read();
}

export function setOwnerPairing(p: OwnerPairing): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(p));
  } catch {
    /* private mode: the pairing lives for this page only */
  }
  window.dispatchEvent(new Event(EVENT));
}

export function clearOwnerPairing(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* nothing stored */
  }
  window.dispatchEvent(new Event(EVENT));
}

function subscribe(cb: () => void) {
  window.addEventListener(EVENT, cb);
  return () => window.removeEventListener(EVENT, cb);
}

/** Reactive read so the Device tab flips between paired/unpaired live. */
export function useOwnerPairing(): OwnerPairing | null {
  return useSyncExternalStore(subscribe, getOwnerPairing);
}

async function detailOf(res: Response): Promise<string | null> {
  try {
    const b = await res.json();
    return b && typeof b.detail === "string" ? b.detail : null;
  } catch {
    return null;
  }
}

/** POST {RELAY_URL}/v0/device/pair {code, username} -> stores the pairing. */
export async function pairOwnerDevice(code: string, username: string): Promise<{ ok: true } | { ok: false; reason: string }> {
  let res: Response;
  try {
    res = await fetch(`${RELAY_URL}/v0/device/pair`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, username }),
    });
  } catch {
    return { ok: false, reason: "Could not reach the relay. Check your connection and try again." };
  }
  if (!res.ok) {
    const detail = await detailOf(res);
    return { ok: false, reason: detail ?? `The relay refused it (${res.status}).` };
  }
  const body = (await res.json()) as DevicePairResponse;
  setOwnerPairing(body);
  return { ok: true };
}

/** DELETE {RELAY_URL}/v0/device/pair with this phone's bearer. Clears the
 * local pairing even when the relay call fails (chris/a2-owner-app step 6);
 * the caller shows `note` when it does. */
export async function unpairOwnerDevice(): Promise<{ ok: true } | { ok: false; note: string }> {
  const owner = getOwnerPairing();
  clearOwnerPairing();
  if (!owner?.token) return { ok: true };
  try {
    const res = await fetch(`${RELAY_URL}/v0/device/pair`, {
      method: "DELETE",
      headers: { Authorization: `Bearer ${owner.token}` },
    });
    if (!res.ok) return { ok: false, note: "Disconnected on this phone, but the relay did not confirm it." };
    return { ok: true };
  } catch {
    return { ok: false, note: "Disconnected on this phone, but Irin's relay could not be reached." };
  }
}
