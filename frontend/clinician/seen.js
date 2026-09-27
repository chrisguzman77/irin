// justin.md R5/R13: the "what Impiricus sees" panel, never cut from the demo.
// GET {relay}/v0/log (public, no bearer) is the relay's own audit of what it
// carried: route, ids, kinds, sizes, ciphertext prefixes, timestamps. The panel
// draws ONLY the fields named in SHOWN below; anything else on a row is counted
// ("1 other field not shown") and never drawn, so no plaintext field can reach
// the screen even if one ever appeared in the log.
const POLL_MS = 5000;
const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
};

// field -> how it reads; every one is an id, a kind, a size, a prefix, or a flag.
const SHOWN = {
  sender: (v) => `from ${v}`,
  recipient: (v) => `to ${v}`,
  device: (v) => `device ${v}`,
  doctor: (v) => `doctor ${v}`,
  doctor_id: (v) => `doctor ${v}`,
  token_prefix: (v) => `token ${v}…`,
  kind: (v) => `kind ${v}`,
  program: (v) => `program ${v}`,
  peer_kind: (v) => `peer ${v}`,
  status: (v) => `status ${v}`,
  by: (v) => `by ${v}`,
  size: (v) => `${v} bytes`,
  ciphertext_prefix: (v) => `ciphertext ${v}…`,
  category: (v) => `category ${v}`,
  brand: (v) => `brand ${v}`,
  is_demo: (v) => (v ? "DEMO" : null),
};
const asUtc = (iso) => (/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
const time = (iso) => new Date(asUtc(String(iso))).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });

let base = "";
let timer = null;

async function refresh() {
  let rows;
  try {
    const res = await fetch(`${base}/v0/log?limit=50`, { cache: "no-store" });
    if (!res.ok) return ($("seenstatus").textContent = `The relay's log answered ${res.status}.`);
    rows = await res.json();
  } catch {
    return ($("seenstatus").textContent = "Cannot reach the relay's log.");
  }
  $("seenstatus").textContent = `${rows.length} most recent entries, newest first. This is everything the courier records.`;
  $("seenlist").replaceChildren(...rows.map((r) => {
    const li = el("li");
    li.append(el("span", "seen-at", time(r.at)), el("code", "seen-route", String(r.route)));
    const chips = el("span", "seen-chips");
    let hidden = 0;
    for (const [k, v] of Object.entries(r)) {
      if (k === "at" || k === "route") continue;
      const f = SHOWN[k];
      if (!f) { hidden++; continue; }
      const text = v === null || v === undefined ? null : f(typeof v === "object" ? "…" : v);
      if (text) chips.append(el("span", k === "is_demo" ? "badge-demo" : "seen-chip", text));
    }
    if (hidden) chips.append(el("span", "seen-chip muted", `${hidden} other field${hidden === 1 ? "" : "s"} not shown`));
    li.append(chips);
    return li;
  }));
}

/** Wires the <details id="seen"> panel to poll `relayUrl`'s log while it is open. */
export function initSeen(relayUrl) {
  base = String(relayUrl).replace(/\/$/, "");
  $("seen").addEventListener("toggle", () => {
    clearInterval(timer);
    if (!$("seen").open) return;
    refresh();
    timer = setInterval(refresh, POLL_MS);
  });
}
