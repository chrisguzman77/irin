import { renderCard } from "./card.js";
import { openPlanForm } from "./plan-form.js";
import { clear, load, openFromDevice, relay, saveSent, sealToDevice, sentMessages } from "./session.js";

// justin.md R5: the clinician inbox ("mock Ascend"). Paired (see pair.js): polls
// GET {relay}/v0/inbox/{doctor_id} every 5 s with the bearer, opens each sealed
// card with this browser's key (a card that does not open from the paired
// device's key is never shown, only counted), orders red, then amber, then one
// digest line for greens, and draws a card with card.js. The card's
// allowed_actions become DoctorMessages the doctor types (every number typed,
// never prefilled: invariant 7), sealed to the device key and POSTed to
// {relay}/v0/messages; nothing applies until the patient confirms on their Irin
// with their PIN (invariant 8), and "Patient confirmed 07:14" comes from GET
// {relay}/v0/messages/{id}. Not paired: the two sample cards, as a preview.
const POLL_MS = 5000;
const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
};

const KIND = {
  basal_check: "Basal Check", hypo_response: "Hypo Response", follow_up: "Follow-up", early_check: "Early check",
  step_check: "Step check", step_gate: "Step gate", safety: "Safety", graduation: "Graduation", baseline_note: "Baseline note",
};
const PROGRAM = { standing: "Standing", step_watch: "Step Watch" };
const SOURCE = { irin_bedside: "Irin Bedside", irin_brain: "Irin Brain" };
const RANK = { red: 0, amber: 1, insufficient: 2, green: 3 };
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const day = (iso) => { const [, m, d] = String(iso).slice(0, 10).split("-").map(Number); return m ? `${MONTHS[m - 1]} ${d}` : ""; };
// The relay's timestamps are UTC but may arrive without an offset (its Mongo
// client is not tz-aware): read a bare timestamp as UTC, never as local time.
const asUtc = (iso) => (/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
const hhmm = (iso) => new Date(asUtc(String(iso))).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });

// ------------------------------------------------------------ not paired: the preview

const FIXTURES = { standing: "fixtures/signal_card_standing.json", step: "fixtures/signal_card_step.json" };
async function preview(name) {
  for (const b of document.querySelectorAll("[data-fixture]")) b.setAttribute("aria-pressed", String(b.dataset.fixture === name));
  try {
    const res = await fetch(FIXTURES[name], { cache: "no-store" });
    if (!res.ok) throw new Error(String(res.status));
    $("sample").replaceChildren(renderCard(await res.json(), { actions: true, sample: true }));
  } catch (e) {
    $("sample").replaceChildren();
    $("out").hidden = false;
    $("out").textContent = `could not load the sample card (${e.message}); serve this folder over http`;
  }
}
function showUnpaired(note) {
  $("live").hidden = true;
  $("unpaired").hidden = false;
  if (note) $("unpaired").querySelector(".note").textContent = note;
  for (const b of document.querySelectorAll("[data-fixture]")) b.addEventListener("click", () => preview(b.dataset.fixture));
  preview(new URLSearchParams(location.search).get("sample") === "step" ? "step" : "standing");
}

// ------------------------------------------------------------ paired: the live inbox

const s = load();
const cards = new Map(); // card_id -> SignalCard (a re-sent card replaces its earlier version)
let since = null;        // the relay's created_at of the newest envelope seen
let unopened = 0;
let openId = null;       // the card on screen
let lastOk = null;
let sent = sentMessages();

async function pollInbox() {
  let res;
  try {
    res = await relay(s, `/v0/inbox/${encodeURIComponent(s.doctor_id)}${since ? `?since=${encodeURIComponent(since)}` : ""}`);
  } catch {
    return status("Cannot reach the relay. Still trying…");
  }
  if (res.status === 401) {
    clear();
    return showUnpaired("The patient stopped sharing with this browser. Scan a new QR code on their Irin to pair again.");
  }
  if (!res.ok) return status(`The relay answered ${res.status}. Still trying…`);
  const rows = await res.json();
  let changed = false;
  for (const env of rows) {
    if (!since || env.created_at > since) since = env.created_at;
    const plain = openFromDevice(s, env.nonce, env.ciphertext);
    let card = null;
    try { card = plain ? JSON.parse(plain) : null; } catch { card = null; }
    // only a card that opened from the paired device's key, in the pairing's world (invariant 11)
    if (!card || typeof card.card_id !== "string" || !!card.is_demo !== !!s.is_demo || !!env.is_demo !== !!s.is_demo) {
      unopened++;
      continue;
    }
    cards.set(card.card_id, card);
    changed = true;
  }
  lastOk = new Date();
  status();
  if (changed || !$("list").childElementCount) render();
}

async function pollMessages() {
  let changed = false;
  for (const m of sent.filter((x) => x.status === "pending")) {
    try {
      const res = await relay(s, `/v0/messages/${encodeURIComponent(m.message_id)}`);
      if (!res.ok) continue;
      const r = await res.json();
      if (r.status && r.status !== m.status) {
        m.status = r.status;
        m.resolved_at = r.resolved_at || null;
        changed = true;
      }
    } catch { /* next round */ }
  }
  if (changed) {
    saveSent(sent);
    render();
  }
}

function status(text) {
  const bits = [];
  if (text) bits.push(text);
  else if (lastOk) bits.push(`Checked ${hhmm(lastOk.toISOString())}.`);
  if (unopened) bits.push(`${unopened} card${unopened === 1 ? "" : "s"} could not be opened with this browser's key and ${unopened === 1 ? "is" : "are"} not shown.`);
  $("status").textContent = bits.join(" ");
}

function resolutionText(m) {
  const at = m.resolved_at ? ` ${hhmm(m.resolved_at)}` : "";
  if (m.status === "confirmed") return `Patient confirmed${at}`;
  if (m.status === "declined") return `Patient declined${at}: nothing changed`;
  if (m.status === "expired") return "Expired unanswered: nothing changed";
  return `Waiting for the patient (sent ${hhmm(m.sent_at)})`;
}

function row(card) {
  const b = el("button", `row st-${card.status}`);
  b.type = "button";
  const top = el("div", "row-top");
  top.append(el("span", `dot st-${card.status}`), el("strong", "", KIND[card.kind] || card.kind),
    el("span", "muted", ` · ${PROGRAM[card.program] || card.program} · ${SOURCE[card.source] || card.source}`));
  if (card.is_demo) top.append(el("span", "badge-demo", "DEMO"));
  const mine = sent.filter((m) => m.card_id === card.card_id);
  if (mine.length) top.append(el("span", `reply r-${mine[mine.length - 1].status}`, resolutionText(mine[mine.length - 1])));
  b.append(top, el("div", "row-head", card.headline), el("div", "row-meta muted", `${day(card.period_start)} – ${day(card.period_end)}`));
  b.addEventListener("click", () => { openId = card.card_id; render(); window.scrollTo(0, 0); });
  return b;
}

function render() {
  const all = [...cards.values()].sort((a, b) =>
    (RANK[a.status] ?? 9) - (RANK[b.status] ?? 9) || String(b.generated_at).localeCompare(String(a.generated_at)));
  const card = openId ? cards.get(openId) : null;
  $("detail").hidden = !card;
  $("list").hidden = !!card;
  if (card) {
    $("card").replaceChildren(renderCard(card, { actions: true, onAction: (key) => openAction(card, key) }));
    const mine = sent.filter((m) => m.card_id === card.card_id);
    $("sent").hidden = !mine.length;
    $("sentlist").replaceChildren(...mine.map((m) => {
      const li = el("li");
      li.append(el("span", "", m.summary), el("span", `reply r-${m.status}`, resolutionText(m)));
      return li;
    }));
    return;
  }
  const loud = all.filter((c) => c.status !== "green");
  const greens = all.filter((c) => c.status === "green");
  const out = [];
  const loose = sent.filter((m) => !m.card_id); // plans: sent from the bar, not from a card
  if (loose.length) {
    const box = el("section", "sent");
    box.append(el("h4", "", "Plans you sent"));
    const ul = el("ul");
    for (const m of loose) {
      const li = el("li");
      li.append(el("span", "", m.summary), el("span", `reply r-${m.status}`, resolutionText(m)));
      ul.append(li);
    }
    box.append(ul);
    out.push(box);
  }
  if (!all.length) out.push(el("p", "note", "No cards yet. Irin sends a card only when there is something to decide, and a green digest weekly."));
  out.push(...loud.map(row));
  if (greens.length) {
    const d = el("details", "digest");
    d.append(el("summary", "", `${greens.length} green card${greens.length === 1 ? "" : "s"}: nothing needs you (weekly digest)`), ...greens.map(row));
    out.push(d);
  }
  $("list").replaceChildren(...out);
}

// ------------------------------------------------------------ actions -> sealed DoctorMessages

const ACTIONS = {
  adjust_basal: { title: "Adjust basal", kind: "insulin_change", fields: ["units", "start"], insulin: "basal" },
  adjust_insulin: { title: "Adjust insulin", kind: "insulin_change", fields: ["insulin", "units", "start"] },
  hold_step: { title: "Hold the next step", kind: "hold_step", fields: ["weeks"] },
  proceed: { title: "Proceed with the next step as planned", kind: "proceed", fields: [] },
  end_watch: { title: "End the Step Watch", kind: "end_watch", fields: [] },
  message: { title: "Message the patient", kind: "note", fields: ["text"] },
  ask_patient: { title: "Ask the patient", kind: "note", fields: ["text"] },
  schedule_visit: { title: "Ask the patient to schedule a visit", kind: "schedule_request", fields: ["note"] },
  dismiss: { title: "Dismiss: no change needed", kind: "dismiss", fields: [] },
};
const MAX_UNITS = 50; // the Pi refuses an insulin change outside (0, 50]

function field(label, input) {
  const l = el("label", "field");
  l.append(el("span", "", label), input);
  return l;
}
function buildFields(a) {
  const out = [];
  for (const f of a.fields) {
    if (f === "units") {
      const i = el("input");
      Object.assign(i, { type: "number", name: "units", min: "0.5", max: String(MAX_UNITS), step: "0.5", required: true, inputMode: "decimal" });
      out.push(field("New dose, units (you type it; nothing is suggested)", i));
    } else if (f === "start") {
      const i = el("input");
      Object.assign(i, { type: "date", name: "start" });
      out.push(field("From (leave empty: from when the patient confirms)", i));
    } else if (f === "insulin") {
      const sel = el("select");
      Object.assign(sel, { name: "insulin", required: true });
      sel.append(Object.assign(el("option", "", "choose…"), { value: "" }), Object.assign(el("option", "", "basal"), { value: "basal" }),
        Object.assign(el("option", "", "bolus"), { value: "bolus" }));
      out.push(field("Which insulin", sel));
    } else if (f === "weeks") {
      const g = el("fieldset", "weeks");
      g.append(el("legend", "", "Hold for"));
      for (const w of [2, 4, 8]) {
        const i = Object.assign(el("input"), { type: "radio", name: "weeks", value: String(w), required: true });
        const l = el("label");
        l.append(i, ` ${w} weeks`);
        g.append(l);
      }
      out.push(g);
    } else if (f === "text" || f === "note") {
      const t = el("textarea");
      Object.assign(t, { name: "text", maxLength: 500, rows: 3, required: f === "text" });
      out.push(field(f === "text" ? "Message" : "Note (optional)", t));
    }
  }
  out.push(el("p", "note", "The patient sees this on their Irin and must confirm it with their PIN. Nothing changes until they do."));
  return out;
}

let acting = null; // {card, key}
function openAction(card, key) {
  const a = ACTIONS[key];
  if (!a) return;
  acting = { card, key };
  $("acttitle").textContent = a.title;
  $("actfields").replaceChildren(...buildFields(a));
  $("actmsg").textContent = "";
  $("actsend").disabled = false;
  $("act").showModal();
}

const hex = (n) => [...crypto.getRandomValues(new Uint8Array(n))].map((b) => b.toString(16).padStart(2, "0")).join("");

async function sendAction(ev) {
  ev.preventDefault();
  if (!acting) return;
  const { card, key } = acting;
  const a = ACTIONS[key];
  const form = new FormData($("actform"));
  const msg = { message_id: `m-${hex(12)}`, kind: a.kind, created_at: new Date().toISOString(), status: "pending" };
  if (card.plan_id) msg.plan_id = card.plan_id;
  let summary = a.title;
  if (a.kind === "insulin_change") {
    const units = Number(form.get("units"));
    if (!Number.isFinite(units) || units <= 0 || units > MAX_UNITS) return ($("actmsg").textContent = `Type a dose above 0 and at most ${MAX_UNITS} units.`);
    msg.insulin = a.insulin || String(form.get("insulin") || "");
    if (msg.insulin !== "basal" && msg.insulin !== "bolus") return ($("actmsg").textContent = "Choose basal or bolus.");
    msg.new_units = units;
    const start = String(form.get("start") || "");
    if (start) msg.start_date = start;
    summary = `${msg.insulin} ${units} units${start ? ` from ${day(start)}` : ", from when confirmed"}`;
  } else if (a.kind === "hold_step") {
    msg.hold_weeks = Number(form.get("weeks"));
    if (![2, 4, 8].includes(msg.hold_weeks)) return ($("actmsg").textContent = "Choose 2, 4 or 8 weeks.");
    summary = `Hold the next step ${msg.hold_weeks} weeks`;
  } else if (a.kind === "note" || a.kind === "schedule_request") {
    const text = String(form.get("text") || "").trim();
    if (a.kind === "note" && !text) return ($("actmsg").textContent = "Type the message.");
    if (text) msg.text = text;
    summary = a.kind === "note" ? `“${text.length > 80 ? text.slice(0, 79) + "…" : text}”` : a.title;
  }
  $("actsend").disabled = true;
  $("actmsg").textContent = "Sending…";
  const refused = await postMessage(msg, summary, card.card_id);
  if (refused) {
    $("actsend").disabled = false;
    return ($("actmsg").textContent = refused);
  }
  $("act").close();
  acting = null;
}

/** Seal a DoctorMessage to the device and POST it; remembers it for "Patient confirmed".
 * Returns null once the relay stored it, or the sentence to show (nothing was sent). */
async function postMessage(msg, summary, cardId = null) {
  try {
    const sealed = sealToDevice(s, msg);
    const res = await relay(s, "/v0/messages", {
      method: "POST",
      body: JSON.stringify({ device_id: s.device_id, message_id: msg.message_id, nonce: sealed.nonce, ciphertext: sealed.ciphertext,
        kind: msg.kind, is_demo: !!s.is_demo }),
    });
    if (!res.ok) {
      const b = await res.json().catch(() => null);
      return `The relay refused it: ${(b && b.detail) || res.status}. Nothing was sent.`;
    }
  } catch {
    return "Cannot reach the relay. Nothing was sent.";
  }
  sent.push({ message_id: msg.message_id, card_id: cardId, kind: msg.kind, summary, sent_at: new Date().toISOString(),
    status: "pending", resolved_at: null });
  saveSent(sent);
  render();
  return null;
}

async function unpair() {
  if (!confirm("Stop receiving this patient's cards? The pairing ends on both sides and this browser forgets its key.")) return;
  try {
    const res = await relay(s, `/v0/pair/${encodeURIComponent(s.doctor_id)}/revoke`, { method: "POST" });
    if (!res.ok && res.status !== 401) return status(`The relay refused (${res.status}). Still paired.`);
  } catch {
    return status("Cannot reach the relay. Still paired.");
  }
  clear();
  location.reload();
}

// ------------------------------------------------------------ start

if (!s || s.state !== "paired" || !s.bearer || !s.doctor_id) {
  showUnpaired(s && s.state === "joining" ? "A pairing is waiting for the patient's confirmation: open the pairing page again from the patient's QR code." : null);
} else {
  $("unpaired").hidden = true;
  $("live").hidden = false;
  $("pairwho").textContent = `Paired as ${s.name}${s.device_id ? ` with ${s.device_id}` : ""}`;
  $("pairdemo").hidden = !s.is_demo;
  $("unpair").addEventListener("click", unpair);
  $("newplan").addEventListener("click", () => openPlanForm({ isDemo: !!s.is_demo, send: (msg, summary) => postMessage(msg, summary) }));
  $("back").addEventListener("click", () => { openId = null; render(); });
  $("actform").addEventListener("submit", sendAction);
  $("actcancel").addEventListener("click", () => { $("act").close(); acting = null; });
  status("Loading…");
  render();
  const tick = () => { pollInbox(); pollMessages(); };
  tick();
  setInterval(tick, POLL_MS);
}
