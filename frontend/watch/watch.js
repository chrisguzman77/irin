import { CLOUD_URL } from "./config.js";
import { clear, load, openFromDevice, relay } from "./session.js";

// justin.md B2 + A6: the watcher page (watch.irin-out-of-sleep-at-hackgt.tech).
// Paired as a buddy (pair.js), it polls GET {relay}/v0/inbox/{peer_id} with the
// bearer and opens each sealed "buddy_alert" envelope with this browser's key:
//   {alert: {alert_id, event_id, urgency, confidence, created_at, status, audio_url},
//    listing: {listing_id, first_name, status, confidence, elapsed_min, urgency,
//              claim_expires_at, treating_expires_at, is_demo},
//    message: one line}
// An envelope that does not open from the paired device's key, or is from the
// other world (demo vs real), is never shown. Only the fields named above are
// read, and a first name or line that carries a number, a place, or a contact is
// dropped (invariant 15): this page never renders a glucose value, a location,
// or a phone number, even if one arrives.
//
// On watch (a tap) holds a screen wake lock and unlocks Web Audio with that
// gesture; a buddy_alert then loops audio_url (the ElevenLabs clip from Irin
// Cloud, or a tone if it will not load) until Call or I've got this. Call and
// I've got this claim the listing (POST /v0/hub/claim), Call then rings through
// the relay (POST /v0/hub/call, no number ever shown), and Execute script shows
// the patient's own script (GET /v0/hub/claim/{id}/script) only while the claim
// is live. The Hub tab lists GET /v0/hub/list in the relay's order (urgency, then
// confidence), with the confidence label always visible.
// Everything here is additive (invariant 13): nothing on this page quiets or
// delays the patient's own alarms.

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
};

const TREATING_MIN = 20;       // relay/hub.py: treating clears the listing for 20 min
const RECENT_MS = 60 * 60e3;   // an open alert older than this, found at page load, does not pop up
const LOG = "irin.watch.log.v0";
const HUB_SEEN = "irin.watch.hub.v0"; // listing_id -> null while seen on the hub, then the resolved time (0 = unknown)

// The relay's timestamps carry an explicit offset (relay/README.md); a bare one,
// from an older relay, is read as UTC, never as local time.
const asUtc = (iso) => (/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
const ms = (iso) => (iso ? Date.parse(asUtc(String(iso))) : NaN);
const hhmm = (t) => new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
const mmss = (n) => `${Math.floor(n / 60)}:${String(n % 60).padStart(2, "0")}`;

// ------------------------------------------------------------ never a number, a place, or a contact

function safeName(text) {
  const n = String(text || "").trim();
  return /^\p{L}[\p{L}' .-]{0,39}$/u.test(n) ? n : "Your buddy";
}
/** The device's one line, or "" if it carries anything but minutes as a number, or a place or contact. */
function safeLine(text) {
  if (typeof text !== "string") return "";
  const line = text.split(/\r?\n/)[0].trim().slice(0, 240);
  const rest = line.replace(/\b\d{1,3}\s*(?:min|mins|minute|minutes)\b/gi, "");
  if (/\d|\bmg\b|mmol|glucose|\bbg\b|@|https?:|www\.|°|\blat\b|\blon\b|street|address|phone/i.test(rest)) return "";
  return line;
}
/** The device's morning line or close-out (buddy_line): a first name, counts and clock times only
 * (relay/README.md). "" if anything but a clock time or a counted unit carries a digit. */
function safeBuddyLine(text) {
  if (typeof text !== "string") return "";
  const line = text.split(/\r?\n/)[0].trim().slice(0, 300);
  const rest = line.replace(/\b\d{1,2}:\d{2}(?:\s*[AaPp]\.?[Mm]\.?)?/g, "")
    .replace(/\b\d{1,3}\s*(?:min|mins|minutes?|hours?|alerts?|calls?|times?|nights?)\b/gi, "");
  if (/\d|\bmg\b|mmol|glucose|\bbg\b|@|https?:|www\.|°|\blat\b|\blon\b|street|address|phone/i.test(rest)) return "";
  return line;
}
const possessive = (name) => (name === "Your buddy" ? "Your buddy's" : `${name}'s`);

// ------------------------------------------------------------ state

const s = load();
const lines = new Map();    // night_date -> {line, receivedAt}: the device's buddy_line (morning line / close-out)
const alerts = new Map();   // alert_id -> {alert, message, receivedAt}; receivedAt is the relay's time (the
                            // alert's own created_at is the device clock, which a replay sets years back)
const byListing = new Map(); // listing_id -> alert_id (the newest alert for it)
const listings = new Map();  // listing_id -> {l, seenAt, prev, reopened, resolvedAt}
const claims = new Map();    // listing_id -> {claim, calledAt, steps} (mine, while live)
const handled = new Set();   // listing_ids silenced by Call / I've got this until they reopen
let hubOrder = [];           // listing_ids in the relay's order
let hubNote = "";
let shown = null;            // listing_id on the alert screen
let since = null;
let firstInbox = true;
let unopened = 0;
let lastOk = null;
let onWatch = false;
let wakeLock = null;
let tab = "buddy";
let alertMsg = "";
let pendingPop = null;

function myLog() {
  try {
    const l = JSON.parse(localStorage.getItem(LOG) || "{}");
    return l && typeof l === "object" ? l : {};
  } catch {
    return {};
  }
}
function logAction(listingId, what) {
  const all = myLog();
  (all[listingId] ||= []).push({ what, at: Date.now() });
  const keep = Object.keys(all).slice(-20);
  try { localStorage.setItem(LOG, JSON.stringify(Object.fromEntries(keep.map((k) => [k, all[k]])))); } catch { /* this session only */ }
}

function mergeListing(l) {
  if (!l || typeof l.listing_id !== "string") return;
  const old = listings.get(l.listing_id);
  const prev = old ? old.l.status : null;
  // elapsed_min changes only when the device re-posts: count on from when this value was first seen
  const seenAt = old && old.l.elapsed_min === l.elapsed_min ? old.seenAt : Date.now();
  const e = { l, seenAt, prev, reopened: old ? old.reopened : false, resolvedAt: old ? old.resolvedAt : null,
    onHub: old ? old.onHub : false };
  if (prev === "treating" && l.status === "open") e.reopened = true;          // treating ran out unrecovered: top urgency
  if (l.status === "resolved" && prev !== "resolved") e.resolvedAt = old ? Date.now() : null;
  if (l.status === "resolved" || l.status === "treating") e.reopened = false;
  // the relay is the truth about my lease: open, resolved, or someone else's expiry means I no longer hold it
  const c = claims.get(l.listing_id);
  if (c && (l.status === "open" || l.status === "resolved"
    || (l.status === "claimed" && l.claim_expires_at && ms(l.claim_expires_at) !== ms(c.claim.expires_at)))) {
    claims.delete(l.listing_id);
  }
  // back to open from claimed or treating, and not held by me: the alert sounds again
  if (l.status === "open" && prev && prev !== "open" && !liveClaim(l.listing_id)) handled.delete(l.listing_id);
  listings.set(l.listing_id, e);
}
const live = (id) => listings.get(id) || null;
function liveClaim(listingId) {
  const c = claims.get(listingId);
  if (!c) return null;
  if (ms(c.claim.expires_at) <= Date.now()) {
    claims.delete(listingId);
    alertMsg = "Your claim lapsed; the listing reopened for other watchers.";
    return null;
  }
  return c;
}
function elapsedMin(e) {
  return Math.max(0, Number(e.l.elapsed_min) || 0) + Math.floor((Date.now() - e.seenAt) / 60e3);
}
function headline(e) {
  const who = possessive(safeName(e.l.first_name));
  const n = elapsedMin(e);
  return e.l.confidence === "device_confirmed"
    ? `${who} alarm has been unacknowledged for ${n} min, presence confirmed`
    : `${who} alarm has been unacknowledged for ${n} min, no response to phone alerts, unconfirmed`;
}
const CONF = { device_confirmed: "Device confirmed", unconfirmed: "Unconfirmed" };
function confBadge(c) {
  const k = c === "device_confirmed" ? "device_confirmed" : "unconfirmed"; // anything unknown is shown as unconfirmed
  return el("span", `conf conf-${k}`, CONF[k]);
}
function treatingAgo(l) {
  const exp = ms(l.treating_expires_at);
  return Number.isFinite(exp) ? Math.max(0, Math.round((Date.now() - (exp - TREATING_MIN * 60e3)) / 60e3)) : null;
}

// ------------------------------------------------------------ audio (A6)

let ctx = null;
let playing = null; // {listingId, src}
const clips = new Map();
let audioNote = "";

function unlockAudio() {
  try {
    ctx ||= new (window.AudioContext || window.webkitAudioContext)();
    ctx.resume();
    const src = ctx.createBufferSource(); // a silent sample inside the gesture unlocks iOS
    src.buffer = ctx.createBuffer(1, 1, 22050);
    src.connect(ctx.destination);
    src.start(0);
  } catch { /* no Web Audio: the screen still shows the alert */ }
}
function toneBuffer() {
  const rate = ctx.sampleRate, b = ctx.createBuffer(1, Math.round(rate * 1.6), rate), d = b.getChannelData(0);
  for (const start of [0, 0.35]) {
    for (let i = 0; i < rate * 0.22; i++) {
      const t = i / rate, env = Math.min(1, t * 80, (0.22 - t) * 80);
      d[Math.round(start * rate) + i] = 0.5 * env * Math.sin(2 * Math.PI * 880 * t);
    }
  }
  return b;
}
/** The clip with a second of silence after it, so the loop is understandable. */
async function clip(url) {
  if (clips.has(url)) return clips.get(url);
  let buf;
  try {
    const u = new URL(url, CLOUD_URL);
    if (u.protocol !== "https:" && u.protocol !== "http:") throw new Error("not a web address");
    const res = await fetch(u, { cache: "force-cache" });
    if (!res.ok) throw new Error(String(res.status));
    const raw = await ctx.decodeAudioData(await res.arrayBuffer());
    buf = ctx.createBuffer(raw.numberOfChannels, raw.length + raw.sampleRate, raw.sampleRate);
    for (let c = 0; c < raw.numberOfChannels; c++) buf.getChannelData(c).set(raw.getChannelData(c));
  } catch {
    return null;
  }
  clips.set(url, buf);
  return buf;
}
async function sound(listingId, url) {
  // already looping (or loading) for this alert; a blocked start retries once a tap has unlocked audio
  if (playing && playing.listingId === listingId && !(playing.blocked && ctx && ctx.state === "running")) return;
  stopAudio();
  const token = { listingId, src: null, blocked: false };
  playing = token;
  if (!ctx || ctx.state !== "running") {
    try { ctx && (await ctx.resume()); } catch { /* blocked until a tap */ }
  }
  if (!ctx || ctx.state !== "running") {
    token.blocked = true; // the 1 s render retries
    audioNote = "Sound is off on this page: tap On watch before bed so an alert can sound.";
    return;
  }
  let buf = url ? await clip(url) : null;
  audioNote = buf ? "" : url ? "The voice clip did not load; playing the alert tone." : "";
  if (!buf) buf = toneBuffer();
  if (playing !== token) return; // stopped while loading
  const src = ctx.createBufferSource();
  src.buffer = buf;
  src.loop = true;
  src.connect(ctx.destination);
  src.start();
  token.src = src;
  renderAlert();
}
function stopAudio() {
  if (playing && playing.src) {
    try { playing.src.stop(); } catch { /* already stopped */ }
  }
  playing = null;
}

// ------------------------------------------------------------ on watch: wake lock + audio

async function lock() {
  if (!("wakeLock" in navigator)) return;
  try {
    wakeLock = await navigator.wakeLock.request("screen");
    wakeLock.addEventListener("release", () => { wakeLock = null; renderWatch(); });
  } catch {
    wakeLock = null;
  }
  renderWatch();
}
function renderWatch() {
  $("onwatch").setAttribute("aria-pressed", String(onWatch));
  $("onwatch").textContent = onWatch ? "On watch: tap to stop" : "On watch";
  if (!onWatch) return ($("watchstate").textContent = "Tap before bed: the screen stays on and an alert can sound.");
  const bits = [];
  bits.push(wakeLock ? "Screen stays on." : "wakeLock" in navigator ? "Screen lock could not be held: keep the screen on by hand." : "This browser cannot keep the screen on: keep it on by hand.");
  bits.push(ctx && ctx.state === "running" ? "Sound is on." : "Sound is blocked: tap On watch again.");
  $("watchstate").textContent = bits.join(" ");
}
$("onwatch").addEventListener("click", async () => {
  onWatch = !onWatch;
  if (onWatch) {
    unlockAudio();
    await lock();
  } else {
    if (wakeLock) wakeLock.release().catch(() => {});
    wakeLock = null;
  }
  renderWatch();
  schedule(0);
});
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible") return;
  if (onWatch && !wakeLock) lock();
  if (ctx) ctx.resume().catch(() => {});
  schedule(0);
});

// ------------------------------------------------------------ the relay

function sharingEnded(note) {
  clear();
  stopAudio();
  shown = null;
  $("alert").hidden = true;
  showUnpaired(note);
}

async function pollInbox() {
  let res;
  try {
    res = await relay(s, `/v0/inbox/${encodeURIComponent(s.doctor_id)}${since ? `?since=${encodeURIComponent(since)}` : ""}`);
  } catch {
    return status("Cannot reach the relay. Still trying…");
  }
  if (res.status === 401) return sharingEnded("Sharing ended: your buddy stopped sharing with this browser. Scan a new QR code on their Irin to pair again.");
  if (!res.ok) return status(`The relay answered ${res.status}. Still trying…`);
  const rows = await res.json();
  let pop = null;
  for (const env of rows) {
    if (!since || env.created_at > since) since = env.created_at;
    if (env.kind === "buddy_line") {
      const plain = openFromDevice(s, env.nonce, env.ciphertext);
      let p = null;
      try { p = plain ? JSON.parse(plain) : null; } catch { p = null; }
      if (!p || p.kind !== "buddy_line" || !!env.is_demo !== !!s.is_demo) {
        unopened++;
        continue;
      }
      const line = safeBuddyLine(p.line);
      if (line) lines.set(String(p.night_date || ""), { line, receivedAt: ms(env.created_at) || Date.now() });
      continue;
    }
    if (env.kind !== "buddy_alert") continue;
    const plain = openFromDevice(s, env.nonce, env.ciphertext);
    let p = null;
    try { p = plain ? JSON.parse(plain) : null; } catch { p = null; }
    const a = p && p.alert, l = p && p.listing;
    // only an alert that opened from the paired device's key, in the pairing's world (invariant 18)
    if (!a || !l || typeof a.alert_id !== "string" || typeof l.listing_id !== "string"
      || !!env.is_demo !== !!s.is_demo || !!l.is_demo !== !!s.is_demo) {
      unopened++;
      continue;
    }
    const isNew = !alerts.has(a.alert_id);
    const receivedAt = ms(env.created_at) || Date.now();
    alerts.set(a.alert_id, { alert: a, message: safeLine(p.message), receivedAt });
    byListing.set(l.listing_id, a.alert_id);
    mergeListing(l);
    // at page load: only a recent alert, and never one this page already saw closed
    const fresh = !firstInbox || (Date.now() - receivedAt < RECENT_MS && typeof hubSeen()[l.listing_id] !== "number");
    if (isNew && l.status === "open" && fresh) {
      handled.delete(l.listing_id);
      pop = l.listing_id;
    }
  }
  firstInbox = false;
  lastOk = new Date();
  status();
  if (pop) {
    if (!hubAt) pendingPop = pop; // page load: pop only once the hub has said whether it is still open
    else showAlert(pop);
  }
  render();
}

async function pollHub() {
  let res;
  try {
    res = await relay(s, "/v0/hub/list");
  } catch {
    hubNote = "Cannot reach the hub. Still trying…";
    return;
  }
  if (res.status === 401) return sharingEnded("Sharing ended: your buddy stopped sharing with this browser. Scan a new QR code on their Irin to pair again.");
  if (res.status === 403) hubNote = "The hub is open only to CGM-verified volunteers in standing who opted in.";
  else if (res.status === 404 || res.status === 501) hubNote = "The hub is not live yet.";
  else if (!res.ok) hubNote = `The hub answered ${res.status}. Still trying…`;
  if (!res.ok) return;
  hubNote = "";
  const rows = await res.json();
  hubOrder = [];
  for (const l of Array.isArray(rows) ? rows : rows.listings || []) {
    if (!l || typeof l.listing_id !== "string") continue;
    mergeListing(l);
    hubOrder.push(l.listing_id);
  }
  // The hub lists every listing not resolved: one of ours that was on it and is gone was resolved
  // (the patient acknowledged, or recovered). "Was on it" is remembered across reloads, so an alert
  // that never reached the hub (hub_watchable off) is never shown as closed.
  const listed = new Set(hubOrder), seen = hubSeen();
  for (const id of listed) if (!(id in seen)) seen[id] = null;
  for (const [id, e] of listings) {
    if (listed.has(id) || e.l.status === "resolved" || !(id in seen)) continue;
    const at = e.onHub ? Date.now() : seen[id];
    mergeListing({ ...e.l, status: "resolved" });
    listings.get(id).resolvedAt = at || null;
    seen[id] = at || 0;
  }
  for (const id of listed) listings.get(id).onHub = true;
  saveHubSeen(seen);
}
function hubSeen() {
  try {
    const m = JSON.parse(localStorage.getItem(HUB_SEEN) || "{}");
    return m && typeof m === "object" ? m : {};
  } catch {
    return {};
  }
}
function saveHubSeen(m) {
  const keep = Object.keys(m).slice(-50);
  try { localStorage.setItem(HUB_SEEN, JSON.stringify(Object.fromEntries(keep.map((k) => [k, m[k]])))); } catch { /* this session only */ }
}

async function claim(listingId) {
  let res;
  try {
    res = await relay(s, "/v0/hub/claim", { method: "POST", body: JSON.stringify({ listing_id: listingId }) });
  } catch {
    alertMsg = "Cannot reach the relay. Try again.";
    return null;
  }
  if (res.status === 409) {
    const b = await res.json().catch(() => ({}));
    const e = live(listingId);
    if (e) e.l = { ...e.l, status: "claimed", claim_expires_at: b.holder_expires_at || e.l.claim_expires_at };
    alertMsg = `Another watcher has this${b.holder_expires_at ? ` until ${hhmm(ms(b.holder_expires_at))}` : ""}. It reopens if they let it lapse.`;
    return null;
  }
  if (!res.ok) {
    alertMsg = `The relay refused the claim (${res.status}).`;
    return null;
  }
  const c = await res.json();
  claims.set(listingId, { claim: c, calledAt: null, steps: null });
  const e = live(listingId);
  if (e) e.l = { ...e.l, status: "claimed", claim_expires_at: c.expires_at };
  alertMsg = "";
  logAction(listingId, "claimed");
  return claims.get(listingId);
}

async function call(listingId) {
  handled.add(listingId);
  stopAudio();
  const c = liveClaim(listingId) || (await claim(listingId));
  if (!c) return render();
  try {
    const res = await relay(s, "/v0/hub/call", { method: "POST", body: JSON.stringify({ claim_id: c.claim.claim_id }) });
    if (res.ok) {
      const b = await res.json().catch(() => ({}));
      c.calledAt = Date.now();
      alertMsg = b.status === "ringing" ? "Ringing: the relay connects the call. No number is shown on either side." : `Call: ${b.status || "sent"}.`;
      logAction(listingId, "called");
    } else if (res.status === 403 || res.status === 404 || res.status === 409 || res.status === 410) {
      claims.delete(listingId);
      alertMsg = "Your claim is no longer live; the call was not placed.";
    } else {
      alertMsg = `The relay could not place the call (${res.status}).`;
    }
  } catch {
    alertMsg = "Cannot reach the relay. Try Call again.";
  }
  render();
}

async function gotIt(listingId) {
  handled.add(listingId);
  stopAudio();
  await claim(listingId);
  render();
}

async function script(listingId) {
  const c = liveClaim(listingId);
  if (!c) return render();
  try {
    const res = await relay(s, `/v0/hub/claim/${encodeURIComponent(c.claim.claim_id)}/script`);
    if (res.ok) {
      const b = await res.json();
      c.steps = (Array.isArray(b.steps) ? b.steps : []).map((x) => (typeof x === "string" ? x : x && typeof x.text === "string" ? x.text : "")).filter(Boolean);
      alertMsg = c.steps.length ? "" : "Your buddy has not written a script.";
      logAction(listingId, "opened the script");
    } else {
      c.steps = null;
      alertMsg = "The script is visible only while your claim is live.";
    }
  } catch {
    alertMsg = "Cannot reach the relay. Try again.";
  }
  render();
}

// ------------------------------------------------------------ the alert screen

function showAlert(listingId) {
  shown = listingId;
  alertMsg = "";
  $("alert").hidden = false;
  renderAlert();
}
$("alert-close").addEventListener("click", () => {
  shown = null;
  stopAudio();
  $("alert").hidden = true;
  render();
});
$("call").addEventListener("click", () => { unlockAudio(); if (shown) call(shown); });
$("gotit").addEventListener("click", () => { unlockAudio(); if (shown) gotIt(shown); });
$("script").addEventListener("click", () => { if (shown) script(shown); });

function renderAlert() {
  if (!shown) return;
  const e = live(shown);
  if (!e) return;
  const l = e.l;
  const aid = byListing.get(shown);
  const a = aid ? alerts.get(aid) : null;
  const who = safeName(l.first_name);
  const mine = liveClaim(shown);
  const st = l.status;
  const other = st === "claimed" && !mine;

  $("alert-demo").hidden = !l.is_demo;
  $("alert-conf").replaceWith(Object.assign(confBadge(l.confidence), { id: "alert-conf" }));
  $("alert-line").textContent = a ? a.message : "";
  $("alert").classList.toggle("calm", st === "resolved" || st === "treating" || other);

  let head, state = "";
  if (st === "resolved") {
    head = `${possessive(who)} alarm is resolved.`;
    state = e.resolvedAt ? `Closed at ${hhmm(e.resolvedAt)}.` : "Closed.";
  } else if (st === "treating") {
    const ago = treatingAgo(l);
    head = `${who} marked treating${ago === null ? "" : ` ${ago} min ago`}.`;
    state = "If it runs out without recovery, the alert comes back at top urgency.";
  } else {
    head = headline(e);
    if (mine) state = `You have this until ${hhmm(ms(mine.claim.expires_at))} (${mmss(Math.max(0, Math.round((ms(mine.claim.expires_at) - Date.now()) / 1e3)))} left).`;
    else if (other) state = `Claimed by another watcher, in progress${l.claim_expires_at ? ` until ${hhmm(ms(l.claim_expires_at))}` : ""}.`;
    else if (e.reopened) state = "Back open at top urgency: treating ran out without recovery.";
  }
  $("alert-head").textContent = head;
  $("alert-state").textContent = [state, alertMsg].filter(Boolean).join(" ");

  const active = st === "open" || (st === "claimed" && mine) || (st === "treating" && mine);
  $("call").hidden = !active;
  $("call").textContent = mine && mine.calledAt ? "Call again" : "Call";
  $("gotit").hidden = !(st === "open" && !mine);
  $("script").hidden = !mine;
  const steps = mine && mine.steps;
  $("steps").hidden = !steps || !steps.length;
  $("steps").replaceChildren(...(steps || []).map((t) => el("li", null, t)));
  $("alert-close").hidden = active && !handled.has(shown);

  // the loop: a buddy alert, open, not yet answered here
  const shouldSound = !!a && st === "open" && !mine && !handled.has(shown);
  if (shouldSound) sound(shown, a.alert.audio_url);
  else if (playing) stopAudio();
  $("alert-audio").textContent = shouldSound ? audioNote : "";
}

// ------------------------------------------------------------ the buddy card and the hub

function renderBuddy() {
  const card = $("buddycard");
  const ids = [...byListing.keys()];
  const last = ids.map(live).filter(Boolean).sort((x, y) => alerts.get(byListing.get(y.l.listing_id)).receivedAt - alerts.get(byListing.get(x.l.listing_id)).receivedAt)[0];
  const dev = [...lines.values()].sort((x, y) => y.receivedAt - x.receivedAt)[0] || null;
  const devLine = (who) => [el("p", "morning", dev.line), el("p", "caveat", `From ${possessive(who)} Irin, ${hhmm(dev.receivedAt)}.`)];
  if (!last) {
    card.replaceChildren(el("h2", null, "Your buddy"), ...(dev ? devLine("Your buddy") : []),
      el("p", "line", "No alerts from your buddy's Irin. If their alarm goes unanswered, this page sounds."));
    return;
  }
  const l = last.l, who = safeName(l.first_name), a = alerts.get(byListing.get(l.listing_id));
  const kids = [el("h2", null, who)];
  const top = el("div", "row-top");
  top.append(confBadge(l.confidence));
  if (l.is_demo) top.append(el("span", "badge-demo", "DEMO"));
  kids.push(top);
  if (l.status === "resolved") {
    // the device's own line once it has spoken after this alert; until then, this page's count
    if (dev && dev.receivedAt >= a.receivedAt) kids.push(...devLine(who));
    else kids.push(el("p", "morning", morningLine(last, who)));
    kids.push(el("p", "line", `Last alert ${hhmm(a.receivedAt)}, resolved${last.resolvedAt ? ` at ${hhmm(last.resolvedAt)}` : ""}.`));
  } else if (l.status === "treating") {
    const ago = treatingAgo(l);
    kids.push(el("p", "line", `Alert open: ${who} marked treating${ago === null ? "" : ` ${ago} min ago`}.`));
  } else {
    kids.push(el("p", "line", `Alert open: ${headline(last)}.`));
  }
  if (a.message) kids.push(el("p", "line", a.message));
  const done = (myLog()[l.listing_id] || []).map((x) => el("li", null, `You ${x.what} at ${hhmm(x.at)}.`));
  if (l.status === "resolved") done.push(el("li", null, `Closed${last.resolvedAt ? ` at ${hhmm(last.resolvedAt)}` : ""}: ${who}'s Irin ended the alert.`));
  if (done.length) {
    const ul = el("ul", "closeout");
    ul.replaceChildren(...done);
    kids.push(ul);
  }
  if (l.status !== "resolved") {
    const b = el("button", "btn open-alert", "Open the alert");
    b.type = "button";
    b.addEventListener("click", () => showAlert(l.listing_id));
    kids.push(b);
  }
  card.replaceChildren(...kids);
}

/** The morning line: the night's alerts from this buddy (counts and times only), all of them closed. */
function morningLine(last, who) {
  const created = (e) => alerts.get(byListing.get(e.l.listing_id)).receivedAt;
  const night = [...byListing.keys()].map(live).filter((e) => e && created(last) - created(e) < 12 * 3600e3);
  const open = night.filter((e) => e.l.status !== "resolved").length;
  const n = night.length;
  const mine = night.some((e) => (myLog()[e.l.listing_id] || []).length);
  return `${n === 1 ? "One alert" : `${n} alerts`} from ${who} overnight${open ? `, ${open} still open` : n === 1 ? ", closed" : ", all closed"}.`
    + (mine ? " Thank you for answering." : "");
}

function renderHub() {
  const box = $("hublist");
  const rows = hubOrder.map(live).filter(Boolean);
  if (!rows.length) {
    box.replaceChildren(el("p", "empty", hubNote || "Nobody needs a backstop right now."));
    return;
  }
  const out = [];
  if (hubNote) out.push(el("p", "empty", hubNote));
  for (const e of rows) {
    const l = e.l, conf = l.confidence === "device_confirmed" ? "device_confirmed" : "unconfirmed";
    const mine = liveClaim(l.listing_id);
    const row = el("article", `listing ${conf} st-${l.status}`);
    const top = el("div", "row-top");
    top.append(el("span", "who", safeName(l.first_name)), confBadge(l.confidence));
    if (l.is_demo) top.append(el("span", "badge-demo", "DEMO"));
    if (e.reopened && l.status === "open") top.append(el("span", "tag top", "Top urgency"));
    if (l.status === "treating") top.append(el("span", "tag treating", "Treating"));
    row.append(top);
    let what, btn = null;
    if (l.status === "open") {
      what = headline(e);
      btn = el("button", "btn", "I've got this");
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        unlockAudio();
        const c = await claim(l.listing_id);
        if (c) showAlert(l.listing_id);
        else render();
      });
    } else if (l.status === "claimed" && mine) {
      what = `You have this until ${hhmm(ms(mine.claim.expires_at))}.`;
      btn = el("button", "btn", "Open");
      btn.addEventListener("click", () => showAlert(l.listing_id));
    } else if (l.status === "claimed") {
      what = "Claimed, in progress.";
    } else if (l.status === "treating") {
      const ago = treatingAgo(l);
      what = `Marked treating${ago === null ? "" : ` ${ago} min ago`}.`;
    } else {
      what = "Resolved.";
    }
    row.append(el("p", "what", what));
    if (btn) {
      btn.type = "button";
      row.append(btn);
    }
    out.push(row);
  }
  box.replaceChildren(...out);
}

function status(text) {
  const bits = [];
  if (text) bits.push(text);
  else if (lastOk) bits.push(`Checked ${hhmm(lastOk)}.`);
  if (unopened) bits.push(`${unopened} alert${unopened === 1 ? "" : "s"} could not be opened with this browser's key and ${unopened === 1 ? "is" : "are"} not shown.`);
  $("status").textContent = bits.join(" ");
}

function render() {
  renderBuddy();
  renderHub();
  renderAlert();
}

for (const b of document.querySelectorAll("[data-tab]")) {
  b.addEventListener("click", () => {
    tab = b.dataset.tab;
    for (const x of document.querySelectorAll("[data-tab]")) x.setAttribute("aria-pressed", String(x === b));
    $("tab-buddy").hidden = tab !== "buddy";
    $("tab-hub").hidden = tab !== "hub";
    schedule(0);
  });
}

$("unpair").addEventListener("click", async () => {
  if (!confirm("End this pairing? Your buddy's alerts stop reaching this browser and its key is deleted.")) return;
  try {
    await relay(s, `/v0/pair/${encodeURIComponent(s.doctor_id)}/revoke`, { method: "POST" });
  } catch { /* the key is forgotten here either way */ }
  sharingEnded("You ended the pairing. Sharing ended.");
});

// ------------------------------------------------------------ WhatsApp (A6's second channel)

// POST {relay}/v0/buddy/whatsapp {phone} (E.164, bearer) -> {registered: true}. The number is
// sent once and never kept or shown here; every refusal is shown as the relay gave it.
$("wa").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const say = (t) => { $("wa-msg").textContent = t; };
  const phone = $("wa-phone").value.replace(/[\s().-]/g, "");
  if (!/^\+[1-9]\d{7,14}$/.test(phone)) return say("Type the number with + and the country code, e.g. +14045550123.");
  const btn = $("wa").querySelector("button");
  btn.disabled = true;
  say("Saving…");
  try {
    const res = await relay(s, "/v0/buddy/whatsapp", { method: "POST", body: JSON.stringify({ phone }) });
    const b = await res.json().catch(() => ({}));
    if (res.ok && b.registered) {
      $("wa-phone").value = "";
      say("Saved. Send \"hi\" once to the Irin WhatsApp number so its messages can reach you.");
    } else if (res.status === 401) {
      sharingEnded("Sharing ended: your buddy stopped sharing with this browser. Scan a new QR code on their Irin to pair again.");
    } else {
      const d = Array.isArray(b.detail) ? b.detail.map((x) => x && x.msg).filter(Boolean).join("; ") : b.detail;
      say(`The relay answered ${res.status}${d ? `: ${d}` : ""}.${res.status === 404 ? " WhatsApp alerts are not live on this relay yet." : ""}`);
    }
  } catch {
    say("Cannot reach the relay. Try again.");
  }
  btn.disabled = false;
});

// ------------------------------------------------------------ the loop

let timer = null;
let hubAt = 0;
let stopped = false;
function schedule(delay) {
  clearTimeout(timer);
  if (!stopped) timer = setTimeout(tick, delay);
}
async function tick() {
  await pollInbox();
  if (stopped) return;
  const hubEvery = onWatch || shown || tab === "hub" ? 3000 : 10000;
  if (Date.now() - hubAt >= hubEvery) {
    hubAt = Date.now();
    await pollHub();
    if (stopped) return;
    if (pendingPop) {
      const e = live(pendingPop);
      if (e && e.l.status === "open") showAlert(pendingPop);
      pendingPop = null;
    }
    render();
  }
  schedule(onWatch || shown ? 1000 : 5000); // on watch: an alert sounds within about a second of reaching the relay
}
setInterval(() => { if (!stopped && shown) renderAlert(); }, 1000);  // lease countdown
setInterval(() => { if (!stopped) { renderBuddy(); renderHub(); } }, 15000); // elapsed minutes

function showUnpaired(note) {
  stopped = true;
  clearTimeout(timer);
  $("paired").hidden = true;
  $("unpaired").hidden = false;
  $("demo").hidden = true;
  if (note) $("unpaired-note").textContent = note;
}

if (!s || s.state !== "paired" || !s.bearer) {
  showUnpaired(s && s.state === "joining" ? "Pairing is not finished: open the pairing page again from your buddy's QR code." : null);
} else {
  $("paired").hidden = false;
  $("demo").hidden = !s.is_demo;
  $("pairwho").textContent = `Watching as ${s.name}${s.paired_at ? `, paired ${new Date(s.paired_at).toLocaleDateString()}` : ""}.`;
  renderWatch();
  render();
  schedule(0);
}
