import { isFreshPath } from "./freshPin";
import { clearPin, getPin } from "./usePin";

export class PinRejected extends Error {
  constructor() {
    super("code not accepted");
  }
}

/** A fresh-PIN verb must go through postFresh (a PIN typed right then). */
export class FreshPinRequired extends Error {
  constructor(path: string) {
    super(`${path} needs a freshly typed PIN: use postFresh`);
  }
}

const MUTATING = new Set(["POST", "PUT", "PATCH", "DELETE"]);

/** Every call to the Pi goes through here. Mutating calls carry X-PIN; a 401
 * clears the stored code so the gate asks again (A1). */
export async function deviceFetch(base: string, path: string, init: RequestInit = {}): Promise<Response> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (MUTATING.has(method)) {
    // never send the stored session code to a fresh-PIN verb (invariant 12);
    // if the Pi's list cannot be read, the Pi is unreachable and so is the verb
    if (await isFreshPath(base, path).catch(() => false)) throw new FreshPinRequired(path);
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
