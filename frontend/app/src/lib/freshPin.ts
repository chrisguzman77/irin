// Fresh-PIN verbs (invariant 12; auth.py require_fresh_pin): pairing confirm
// and doctor-message confirm/decline. Their PIN must come from a prompt shown
// right then, NEVER from storage: not the session code, not a cache. The list
// is contracts.FRESH_PIN_ENDPOINTS, read from the Pi (GET
// /api/contracts/fresh_pin), never hand-copied.
//
// postFresh() is the only way to call them; deviceFetch() refuses them.

type Prompter = (title: string) => Promise<string | null>;

let prompter: Prompter | null = null;
let canceller: (() => void) | null = null;
/** FreshPinPrompt (mounted once in the app shell) registers itself here. */
export function registerPrompter(p: Prompter | null, cancel: (() => void) | null = null): void {
  prompter = p;
  canceller = cancel;
}
/** Close an open prompt as if cancelled (its question no longer exists). */
export function cancelFreshPrompt(): void {
  canceller?.();
}

const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
/** "/api/rounds/messages/{message_id}/confirm" -> one path segment per {} */
export const templateToRegExp = (t: string) => new RegExp("^" + t.split(/\{[^}]+\}/).map(escapeRe).join("[^/]+") + "$");

const lists = new Map<string, Promise<RegExp[]>>();
function patterns(base: string): Promise<RegExp[]> {
  let p = lists.get(base);
  if (!p) {
    p = fetch(`${base}/api/contracts/fresh_pin`, { cache: "no-store" })
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status}`);
        return r.json() as Promise<{ endpoints?: string[] }>;
      })
      .then((b) => (b.endpoints ?? []).map(templateToRegExp));
    p.catch(() => lists.delete(base)); // retry next time; never cache a failure
    lists.set(base, p);
  }
  return p;
}

/** true when `path` (no query) is a fresh-PIN endpoint on this Pi */
export async function isFreshPath(base: string, path: string): Promise<boolean> {
  const bare = path.split("?")[0];
  return (await patterns(base)).some((re) => re.test(bare));
}

export type FreshResult =
  | { ok: true; value: unknown }
  | { ok: false; cancelled?: true; badPin?: true; status?: number; reason: string };

/** POST a fresh-PIN verb: always prompts, sends only what was just typed,
 * stores nothing, and never touches the session code (a wrong PIN here does
 * not log the app out). Refuses a path that is not on the Pi's list. */
export async function postFresh(base: string, path: string, body?: unknown, title = "Enter your PIN to confirm"): Promise<FreshResult> {
  if (!(await isFreshPath(base, path))) throw new Error(`${path} is not a fresh-PIN endpoint`);
  if (!prompter) throw new Error("no PIN prompt mounted");
  const pin = await prompter(title);
  if (!pin) return { ok: false, cancelled: true, reason: "Cancelled. Nothing was sent." };
  try {
    const res = await fetch(base + path, {
      method: "POST",
      headers: { "X-PIN": pin, "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (res.status === 401) return { ok: false, badPin: true, reason: "That PIN was not accepted. Nothing was confirmed." };
    if (!res.ok) return { ok: false, status: res.status, reason: `Your Irin refused it (${res.status}).` };
    return { ok: true, value: await res.json().catch(() => null) };
  } catch {
    return { ok: false, reason: "Could not reach your Irin. Nothing was confirmed." };
  }
}
