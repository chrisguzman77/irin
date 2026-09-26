import { useSyncExternalStore } from "react";

// The code gate (justin.md rule 2 and A1): entered once, kept in
// sessionStorage (closing the tab forgets it), sent as the X-PIN header on
// every mutating call. Never in a URL, never in localStorage on the phone.
const PIN_KEY = "irin.pin";
const EVENT = "irin:pin";

export function getPin(): string | null {
  try {
    return sessionStorage.getItem(PIN_KEY);
  } catch {
    return null;
  }
}

export function savePin(pin: string): void {
  try {
    sessionStorage.setItem(PIN_KEY, pin);
  } catch {
    /* private mode: the PIN lives for this page only */
  }
  window.dispatchEvent(new Event(EVENT));
}

/** Called on a 401: the stored code was wrong, so the gate shows again. */
export function clearPin(rejected = false): void {
  try {
    sessionStorage.removeItem(PIN_KEY);
  } catch {
    /* nothing stored */
  }
  lastRejected = rejected;
  window.dispatchEvent(new Event(EVENT));
}

let lastRejected = false;
export const pinWasRejected = () => lastRejected;

function subscribe(cb: () => void) {
  window.addEventListener(EVENT, cb);
  return () => window.removeEventListener(EVENT, cb);
}

export function usePin(): string | null {
  return useSyncExternalStore(subscribe, getPin);
}
