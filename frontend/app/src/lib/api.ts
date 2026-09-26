import { clearPin, getPin } from "./usePin";

export class PinRejected extends Error {
  constructor() {
    super("code not accepted");
  }
}

const MUTATING = new Set(["POST", "PUT", "PATCH", "DELETE"]);

/** Every call to the Pi goes through here. Mutating calls carry X-PIN; a 401
 * clears the stored code so the gate asks again (A1). */
export async function deviceFetch(base: string, path: string, init: RequestInit = {}): Promise<Response> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (MUTATING.has(method)) {
    headers.set("X-PIN", getPin() ?? "");
    if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  }
  const res = await fetch(base + path, { ...init, method, headers });
  if (res.status === 401) {
    clearPin(true);
    throw new PinRejected();
  }
  return res;
}
