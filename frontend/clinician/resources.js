// justin.md R5/R13: the resources handoff, the pharma moment of a card. Only on a
// card whose resource_categories is non-empty (off-label use gets none). The
// doctor picks the CATEGORY first, then a brand, then sees the mock Ascend
// handoff screen listing exactly what leaves this inbox; "Hand off" POSTs
// {relay}/v0/resources/request {doctor_id: our own, category, brand} with the
// bearer, and the screen shows the relay's answer, its banner text verbatim
// ("No patient data shared with any manufacturer"). Nothing about the patient,
// the card, or any number is ever sent. GET {relay}/v0/resources lists past handoffs.
import { relay } from "./session.js";

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
};

// The seven categories (relay/README.md, the same tokens a card carries).
export const CATEGORY = {
  glucagon_access: "Glucagon access",
  gi_side_effect_education: "GI side-effect education",
  copay_savings: "Copay savings",
  samples_next_pen: "Samples of the next pen strength",
  bridge_supply: "Bridge supply",
  prior_auth_hub: "Prior authorization hub",
  ask_msl: "Ask a medical science liaison",
};
const GLP = ["Mounjaro", "Ozempic", "Trulicity"];
const BRANDS = {
  glucagon_access: ["Baqsimi", "Gvoke", "Zegalogue"],
  gi_side_effect_education: GLP,
  copay_savings: [...GLP, "Awiqli"],
  samples_next_pen: GLP,
  bridge_supply: [...GLP, "Awiqli"],
  prior_auth_hub: [...GLP, "Awiqli"],
  ask_msl: [...GLP, "Awiqli", "Baqsimi", "Gvoke"],
};
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const asUtc = (iso) => (/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
const when = (iso) => {
  const d = new Date(asUtc(String(iso)));
  return `${MONTHS[d.getMonth()]} ${d.getDate()} ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false })}`;
};

let s = null;
let pick = { category: null, brand: null };
let onDone = () => {};

function button(text, cls, fn) {
  const b = Object.assign(el("button", cls, text), { type: "button" });
  b.addEventListener("click", fn);
  return b;
}

function stepCategory(categories) {
  pick = { category: null, brand: null };
  $("restitle").textContent = "Resources: choose a category";
  const list = el("div", "reschoices");
  for (const c of categories) list.append(button(CATEGORY[c] || c, "reschoice", () => { pick.category = c; stepBrand(); }));
  $("resbody").replaceChildren(el("p", "note", "What would help this patient? Nothing about them is shared."), list);
  $("resback").hidden = true;
}

function stepBrand() {
  $("restitle").textContent = `${CATEGORY[pick.category]}: choose a brand`;
  const list = el("div", "reschoices");
  for (const b of BRANDS[pick.category] || []) list.append(button(b, "reschoice", () => { pick.brand = b; stepHandoff(); }));
  $("resbody").replaceChildren(list);
  $("resback").hidden = false;
  $("resback").onclick = () => stepCategory(current);
}

function stepHandoff() {
  $("restitle").textContent = "Ascend · resources handoff";
  const screen = el("div", "ascend");
  screen.append(el("div", "ascend-brand", "Ascend (mock)"));
  const t = el("table", "ascend-fields");
  for (const [k, v] of [["Category", CATEGORY[pick.category]], ["Brand", pick.brand], ["Requested by (your inbox id)", s.doctor_id]]) {
    const tr = el("tr");
    tr.append(el("td", "", k), el("td", "", v));
    t.append(tr);
  }
  screen.append(el("p", "note", "Only these fields leave this inbox. No patient, no card, no number."), t);
  const msg = el("p", "msg");
  const go = button("Hand off", "resgo", async () => {
    go.disabled = true;
    msg.textContent = "Handing off…";
    let res;
    try {
      res = await relay(s, "/v0/resources/request", {
        method: "POST",
        body: JSON.stringify({ doctor_id: s.doctor_id, category: pick.category, brand: pick.brand }),
      });
    } catch {
      go.disabled = false;
      return (msg.textContent = "Cannot reach the relay. Nothing was handed off.");
    }
    const body = await res.json().catch(() => null);
    if (!res.ok || !body) {
      go.disabled = false;
      return (msg.textContent = `The relay refused it: ${(body && body.detail) || res.status}. Nothing was handed off.`);
    }
    stepDone(body);
  });
  screen.append(go, msg);
  $("resbody").replaceChildren(screen);
  $("resback").onclick = stepBrand;
}

function stepDone(r) {
  $("restitle").textContent = "Ascend · handed off";
  const screen = el("div", "ascend");
  screen.append(el("div", "ascend-brand", "Ascend (mock)"), el("div", "ascend-banner", r.banner));
  screen.append(el("p", "", `${CATEGORY[r.category] || r.category} · ${r.brand}: ${r.status === "handed_off" ? "handed off" : r.status}`));
  screen.append(el("p", "note", `Request ${r.request_id}. Fields shared: ${(r.shared_fields || []).join(", ")}.`));
  $("resbody").replaceChildren(screen);
  $("resback").hidden = true;
  onDone();
}

let current = [];
let wired = false;
/** @param {object} session the paired session; @param {string[]} categories the card's resource_categories
 * @param {() => void} done called after a handoff (to refresh the history)
 * @param {string} [category] the category tapped on the card: the flow opens at its brands */
export function openResources(session, categories, done, category) {
  s = session;
  onDone = done || (() => {});
  current = categories.filter((c) => c in CATEGORY);
  if (!wired) {
    wired = true;
    $("resclose").addEventListener("click", () => $("res").close());
  }
  stepCategory(current);
  if (category && current.includes(category)) {
    pick.category = category;
    stepBrand();
  }
  $("res").showModal();
}

/** The doctor's past handoffs, newest first, into the element with id `into`. */
export async function loadHandoffs(session, into) {
  const box = $(into);
  let rows;
  try {
    const res = await relay(session, "/v0/resources");
    if (!res.ok) return box.replaceChildren(el("p", "note", `Past handoffs unavailable (the relay answered ${res.status}).`));
    rows = await res.json();
  } catch {
    return box.replaceChildren(el("p", "note", "Past handoffs unavailable: cannot reach the relay."));
  }
  if (!rows.length) return box.replaceChildren(el("p", "note", "No handoffs yet."));
  const ul = el("ul");
  for (const r of rows) {
    const li = el("li");
    li.append(el("span", "", `${CATEGORY[r.category] || r.category} · ${r.brand}`), el("span", "reply", when(r.at)));
    ul.append(li);
  }
  box.replaceChildren(ul);
}
