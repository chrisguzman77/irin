// Kiosk display: detail screen (justin.md step 1) and the Detail / Night /
// Morning modes (step 2). The mode is the backend's display_mode
// (GET /api/scheduler), never the browser's clock.
// Alarm visuals (step 3): the tier comes from alarm.trigger_type, the
// intensity from alarm.state; the takeover covers every mode.
// Renders ONLY from the state_snapshot sent on every WebSocket connect plus the
// updates after it (hard client rule 1); never assumes it saw messages while
// disconnected. Time on the graph is the Pi's clock (reading timestamps),
// never the browser's.
// Fresh-PIN verbs (pairing confirm, doctor-message confirm/decline) read their
// list from GET /api/contracts/fresh_pin, never a hand copy, and ALWAYS
// re-prompt the keypad (postFresh), cached PIN or not (invariant 12).
const $ = (id) => document.getElementById(id);

const WINDOW_MIN = 180;        // graph history span
const GAP_MIN = 15;            // don't join points across a gap this long (matches STALE_AFTER_MIN)
const DISCONNECT_BANNER_MS = 15000;
const TARGET_LOW = 70;         // time-in-range band, consensus 70-180 mg/dL
const TARGET_HIGH = 180;
const LINE_IN = "#EFEEEA";     // graph line in range (70-180): brand Off-white
const LINE_HIGH = "#ffd60a";   // graph line above range: yellow
const LINE_LOW = "#ff3b30";    // graph line below range: red
const POLL_MS = 5000;          // the Pi's clock and display mode, polled (at 60x replay: 5 clock-min)
const MODES = ["detail", "night", "morning"];

// U+FE0E forces the text glyph: some platforms draw ↗ ↘ as colour emoji.
const TREND_ARROWS = Object.fromEntries(Object.entries({
  DoubleUp: "⇈", SingleUp: "↑", FortyFiveUp: "↗", Flat: "→",
  FortyFiveDown: "↘", SingleDown: "↓", DoubleDown: "⇊",
}).map(([k, v]) => [k, v + "\uFE0E"]));

const state = {
  mode: null,
  latest: null,        // Reading
  forecast: null,      // Forecast, only while forecast status is ok
  forecastNote: "",    // why there is no forecast line (status suspended / unavailable)
  displayMode: "detail", // the backend's display_mode
  settings: null,
  clockSynced: true,
  points: [],          // [{t: ms, mgdl}] oldest first, readings received this session
  connected: false,
  downSince: Date.now(),
  piClock: null,       // ms, the Pi's clock.now() from /api/health
  piDate: null,        // "YYYY-MM-DD" of the Pi's clock, to match a report's night_date
  report: null,        // MorningReport (GET /api/reports/latest), fetched in morning mode
  alarm: { state: "idle", trigger_type: null },  // AlarmState
  presence: null,      // PresenceState (snapshot presence + presence_change): drives the idle screen
  basalNudge: "none",  // the scheduler's basal nudge level: none | visual | email
  familyStories: [],   // FamilyStory list of the latest night (snapshot family_story_status + updates)
  doctorMessages: [],  // pending DoctorMessage list (snapshot pending_doctor_messages + updates)
  doctorName: null,    // the one paired doctor's display name (irinDoctorName of pairing_state)
  doctorSenders: {},   // message_id -> that message's own doctor_display_name (e.g. the simulated Spark)
  doctorMsg: "",
  doctorAsking: null,  // message_id the keypad is open for
  pairing: {},         // pairing_state (snapshot + pairing_state messages)
  planState: {},       // Step Watch plan_state (snapshot + plan_state messages); {active: false} = no watch
  activePlan: null,    // the snapshot's active_plan (TitrationPlan), for the fallback in renderWatch
  ackMsg: "",
};

const PIN_KEY = "irin.kiosk_pin"; // kiosk-only concession: the low-stakes ack PIN lives in localStorage
const LOW_TRIGGERS = ["predicted_low", "actual_low"];
const SOUNDING = ["pending", "active", "rearmed"]; // pending = the predicted-low warning

const toMs = (iso) => new Date(iso).getTime(); // naive Pi-local timestamps; only differences matter
// Every time on the kiosk is 12-hour with AM/PM (George's choice): "9:29 PM".
// Chromium puts a narrow no-break space (U+202F) before AM/PM; Nunito has no
// glyph for it, so it becomes a plain space.
const plainSpace = (s) => s.replace(/[\u202f\u00a0]/g, " ");
const hm12 = (ms) => plainSpace(new Date(ms).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", hour12: true }));
// A settings time ("21:00", HH:MM on the Pi) in the same 12-hour form: "9:00 PM"
const settingTime12 = (hhmm) => {
  const [h, m] = String(hhmm).split(":").map(Number);
  return Number.isInteger(h) && Number.isInteger(m) ? hm12(new Date(2000, 0, 1, h, m).getTime()) : String(hhmm);
};
const hour12 = (ms) => plainSpace(new Date(ms).toLocaleTimeString("en-US", { hour: "numeric", hour12: true }));  // graph axis: "7 PM"

// --- data ---

function addReading(r) {
  if (!r) return;
  const t = toMs(r.timestamp);
  const last = state.points[state.points.length - 1];
  if (last && t < last.t) state.points = [];          // replay restarted or clock moved back
  if (!last || t > last.t) state.points.push({ t, mgdl: r.glucose_mgdl });
  const cutoff = t - WINDOW_MIN * 60000;
  while (state.points.length && state.points[0].t < cutoff) state.points.shift();
  state.latest = r;
}

function setMode(mode) {
  if (state.mode !== null && mode !== state.mode) {   // live <-> demo: never mix the two on one graph
    state.points = [];
    state.forecast = null;
    state.latest = null;
  }
  state.mode = mode;
}

// After every snapshot (connect, reconnect, mode switch) the graph is refilled
// from GET /api/history, so a reload never starts from one point.
let historyToken = 0;
async function backfillHistory() {
  const token = ++historyToken;
  const mode = state.mode;
  try {
    const res = await fetch(`/api/history?minutes=${WINDOW_MIN}`, { cache: "no-store" });
    if (!res.ok) return;
    const rows = await res.json();
    if (token !== historyToken || mode !== state.mode) return; // superseded or the source switched
    const seen = new Map(state.points.map((p) => [p.t, p]));
    for (const r of rows) seen.set(toMs(r.timestamp), { t: toMs(r.timestamp), mgdl: r.glucose_mgdl });
    const newest = state.latest ? toMs(state.latest.timestamp) : Math.max(...seen.keys());
    state.points = [...seen.values()]
      .filter((p) => p.t <= newest && p.t >= newest - WINDOW_MIN * 60000)
      .sort((a, b) => a.t - b.t);
    render();
  } catch { /* the graph keeps what it has; the disconnected banner covers a dead backend */ }
}

// forecast_update carries status ok | suspended | unavailable; only ok draws a line.
function setForecast(p) {
  const ok = p && p.status === "ok" && p.predicted_mgdl != null;
  state.forecast = ok ? p : null;
  state.forecastNote = ok || !p || !p.status ? "" : `forecast ${p.status}${p.reason ? `: ${p.reason}` : ""}`;
}

function onMessage(msg) {
  const p = msg.payload || {};
  switch (msg.type) {
    case "state_snapshot":
      setMode(p.mode);
      state.settings = p.settings || null;
      state.clockSynced = p.clock_synced !== false;
      state.forecast = p.forecast || null;
      state.forecastNote = "";
      state.alarm = p.alarm || { state: "idle", trigger_type: null };
      state.presence = p.presence || null;
      state.familyStories = Array.isArray(p.family_story_status) ? p.family_story_status : [];
      state.doctorMessages = Array.isArray(p.pending_doctor_messages) ? p.pending_doctor_messages : [];
      loadDoctorSenders();
      setPairing(p.pairing_state);
      state.planState = p.plan_state || {};
      state.activePlan = p.active_plan || null;
      if (p.latest_reading) addReading(p.latest_reading);
      else state.latest = null;
      backfillHistory();
      break;
    case "reading_update":
      addReading(p);
      break;
    case "forecast_update":
      setForecast(p);
      break;
    case "mode_change":
      setMode(p.mode);
      backfillHistory();
      pollDevice();
      break;
    case "alarm_state_change":
      state.alarm = p.alarm || p;
      if (!SOUNDING.includes(state.alarm.state)) state.ackMsg = "";
      break;
    case "doctor_message_received": {
      const m = p.message || p;
      if (m && typeof m.message_id === "string" && (m.status || "pending") === "pending")
        state.doctorMessages = state.doctorMessages.filter((x) => x.message_id !== m.message_id).concat([m]);
      if (m && typeof m.message_id === "string" && typeof p.doctor_display_name === "string" && p.doctor_display_name.trim())
        state.doctorSenders[m.message_id] = p.doctor_display_name.trim();
      break;
    }
    case "doctor_message_resolved": {
      const id = p.message_id || (p.message && p.message.message_id);
      state.doctorMessages = state.doctorMessages.filter((x) => x.message_id !== id);
      state.doctorMsg = "";
      if (state.doctorAsking === id) cancelKeypad(); // settled elsewhere: never confirm a message that is gone
      break;
    }
    case "family_story_pending":
    case "family_story_sent":
      mergeStory(p);
      break;
    case "settings_change":
      state.settings = p.settings || p;
      break;
    case "pairing_state":
      setPairing(p);
      break;
    case "plan_state":
      state.planState = p || {};
      break;
    case "presence_change":
      state.presence = p;
      break;
    default:
      return; // other types belong to later steps; the hub's echo replies have no type
  }
  render();
}

// One night's stories: a newer night replaces the list, the same night updates in place.
function mergeStory(story) {
  if (!story || typeof story.story_id !== "string") return;
  const night = state.familyStories[0] && state.familyStories[0].night_date;
  if (night && story.night_date > night) state.familyStories = [];
  state.familyStories = state.familyStories.filter((s) => s.story_id !== story.story_id).concat([story]);
}

// --- socket ---

let ws = null;
let retryMs = 500;

function connect() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${proto}//${location.host}/ws`);
  ws.onopen = () => { state.connected = true; retryMs = 500; render(); };
  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg && msg.type) onMessage(msg);
  };
  ws.onclose = () => {
    if (state.connected) state.downSince = Date.now();
    state.connected = false;
    render();
    setTimeout(connect, retryMs);
    retryMs = Math.min(retryMs * 2, 5000);
  };
  ws.onerror = () => ws.close();
}

// --- the Pi's clock and the screen mode ---

// The Pi's clock (/api/health, for the night clock face) and the backend's
// display_mode and clock_synced (/api/scheduler, the NTP guard).
async function pollDevice() {
  try {
    const [h, s] = await Promise.all([
      fetch("/api/health", { cache: "no-store" }).then((r) => r.json()),
      fetch("/api/scheduler", { cache: "no-store" }).then((r) => r.json()),
    ]);
    const t = toMs(h.clock);
    if (!Number.isNaN(t)) state.piClock = t;
    if (typeof h.clock === "string") state.piDate = h.clock.slice(0, 10);
    state.displayMode = MODES.includes(s.display_mode) ? s.display_mode : "detail";
    if (typeof s.clock_synced === "boolean") state.clockSynced = s.clock_synced;
    state.basalNudge = (s.basal_nudge && s.basal_nudge.level) || "none";
    if (screenMode() === "morning") await fetchReport();
  } catch { /* the disconnected banner covers a dead backend */ }
  render();
}

// The Morning numbers come from the morning report (step 11 on the backend),
// only while the screen is in morning mode.
async function fetchReport() {
  const res = await fetch("/api/reports/latest", { cache: "no-store" });
  if (res.status === 404) { state.report = null; return; }
  if (res.ok) state.report = await res.json();
}

// Detail whenever the clock is not synced: no trusted clock, no night decision.
const screenMode = () => (state.clockSynced ? state.displayMode : "detail");

// --- render ---

const isDisconnected = () => !state.connected && Date.now() - state.downSince > DISCONNECT_BANNER_MS;

// The 30-min prediction, shown as TEXT under the current number and as the
// DOTTED graph line, in one colour. forecast_v1 is a 20th-percentile forecast
// ("how low it could plausibly get"), so the words say "could be as low as",
// never a bare "predicted". It updates on every forecast_update (each new
// reading, ~5 min) and, like the dotted line, only on fresh data (invariant 1).
const PREDICTION_COLOR = "#B1D2BD";

function predictionText() {
  const r = state.latest;
  const fc = state.forecast;
  if (!fc || !r || r.is_stale || isDisconnected() || fc.predicted_mgdl == null) return null;
  return `could be as low as ${Math.round(fc.predicted_mgdl)} in ${fc.horizon_min ?? 30} min`;
}

function render() {
  const r = state.latest;
  const stale = !r || r.is_stale;
  const disconnected = isDisconnected();

  $("badge-demo").classList.toggle("hidden", state.mode !== "replay");
  $("badge-clock").classList.toggle("hidden", state.clockSynced);
  const a = state.alarm || {};
  const staleAlarm = a.trigger_type === "stale" && a.state !== "idle";
  $("banner-stale").classList.toggle("hidden", !((r && r.is_stale) || staleAlarm));
  $("banner-disconnected").classList.toggle("hidden", !disconnected);
  // basal nudge (backend step 10): visual at 60 min past the usual basal time
  // with no basal logged; a glance reminder only, logging lives in the app
  const nudge = state.basalNudge !== "none";
  $("flag-basal").classList.toggle("hidden", !nudge);
  if (nudge) {
    const usual = state.settings && state.settings.basal_time;
    $("flag-basal").textContent = usual ? `basal not logged yet · usual ${settingTime12(usual)}` : "basal not logged yet";
  }
  document.body.classList.toggle("is-stale", stale);
  document.body.classList.toggle("is-disconnected", disconnected);

  const mode = screenMode();
  for (const m of MODES) {
    document.body.classList.toggle(`mode-${m}`, m === mode);
    $(m).classList.toggle("hidden", m !== mode);
  }
  const num = r ? String(Math.round(r.glucose_mgdl)) : "---";
  const arrow = r && !r.is_stale ? (TREND_ARROWS[r.trend] ?? "?") : "";
  $("night-glucose").textContent = $("morning-glucose").textContent = num;
  $("night-trend").textContent = $("morning-trend").textContent = arrow;
  const now = state.piClock ?? (r ? toMs(r.timestamp) : null);
  // the night clock: the time big, AM/PM small beside it (a full-size "AM" would
  // make an already huge clock much wider)
  if (now === null) {
    $("night-clock").textContent = "--:--";
  } else {
    const [hm, ap] = hm12(now).split(" ");
    $("night-clock").textContent = hm;
    const suffix = document.createElement("span");
    suffix.className = "ampm";
    suffix.textContent = ap;
    $("night-clock").append(suffix);
  }
  // top-right clock: the Pi's time, blank until the Pi's clock has synced (no
  // RTC battery: an unsynced wall clock is untrusted and is never shown as the time)
  $("topclock").textContent = now === null || !state.clockSynced ? "" : hm12(now);
  const pred = predictionText();
  for (const id of ["prediction", "night-prediction"]) {
    $(id).textContent = pred ?? "";
    $(id).classList.toggle("hidden", pred === null);
  }
  if (mode === "morning") renderMorning();

  if (r) {
    $("glucose").textContent = Math.round(r.glucose_mgdl);
    $("trend").textContent = r.is_stale ? "" : (TREND_ARROWS[r.trend] ?? "?");
    $("reading-time").textContent = `reading at ${hm12(toMs(r.timestamp))}`;
  } else {
    $("glucose").textContent = "---";
    $("trend").textContent = "";
    $("reading-time").textContent = state.connected ? "no reading yet" : "connecting to device…";
  }
  // IOB, last dose, and today's TIR are not in the snapshot yet (contracts
  // request to Chris, see journal); the tiles stay "—" until they are.
  renderAlarm(num, arrow);
  renderWatch();
  renderPair();
  renderDoctor();
  if (mode === "detail") drawGraph();
  updateIdle();
}

// --- idle screen ---
// Shown only while the Pi's presence is Away (nobody in the radar's range for
// AWAY_AFTER_MIN; never at night, backend rule) AND nothing needs a person:
// any alarm state other than idle (warning, low, acknowledged, re-armed,
// stale, high) or a reading below the low threshold keeps the normal screen.
// Waking because someone is back plays the 2 s flood-and-fall (below); waking
// because of an alarm or a low is instant, flood and all (the alarm takeover
// is above it anyway). It only reads state and sends nothing.
const WAKE_MS = 2000;
let idleShown = false;
let wakeTimer = null;

// The flood: leaves pop in on a jittered grid until they cover the screen,
// then each falls off the bottom. The timings match style.css (#idle.waking
// fades the dusk out at 0.35-0.7 s, under the covering leaves) and end inside
// WAKE_MS: pops start 0-0.35 s (0.25 s each), falls start 0.65-1.0 s and last
// 0.65-0.9 s, so the last leaf is gone by 1.9 s.
const FLOOD_COLS = 12, FLOOD_ROWS = 8;
const LEAF_IMAGES = ["leaf-mint.png", "leaf-sage.png"];
for (const src of LEAF_IMAGES) new Image().src = src; // loaded before the first wake, so no leaf pops in late
const rand = (lo, hi) => lo + Math.random() * (hi - lo);

function floodLeaves() {
  const leaves = [];
  for (let i = 0; i < FLOOD_COLS * FLOOD_ROWS; i++) {
    const col = i % FLOOD_COLS, row = Math.floor(i / FLOOD_COLS);
    const leaf = document.createElement("div");
    leaf.className = "flood-leaf";
    leaf.style.left = `${((col + rand(0.1, 0.9)) / FLOOD_COLS) * 100}%`;
    leaf.style.top = `${((row + rand(0.1, 0.9)) / FLOOD_ROWS) * 100}%`;
    leaf.style.width = `${rand(9, 16)}vmin`;
    leaf.style.setProperty("--pop-at", `${Math.round(rand(0, 350))}ms`);
    leaf.style.setProperty("--fall-at", `${Math.round(rand(650, 1000))}ms`);
    leaf.style.setProperty("--fall-ms", `${Math.round(rand(650, 900))}ms`);
    leaf.style.setProperty("--turn", `${Math.round(rand(-180, 180))}deg`);
    leaf.style.setProperty("--spin", `${Math.round(rand(-120, 120))}deg`);
    const img = document.createElement("img");
    img.src = LEAF_IMAGES[i % 2];
    img.alt = "";
    leaf.append(img);
    leaves.push(leaf);
  }
  $("idle-flood").replaceChildren(...leaves);
}
function clearFlood() {
  $("idle-flood").replaceChildren();
}

function needsPerson() {
  const a = state.alarm || {};
  if (a.state && a.state !== "idle") return true;
  const r = state.latest;
  const low = (state.settings && Number(state.settings.low_threshold)) || 70;
  return !!(r && typeof r.glucose_mgdl === "number" && r.glucose_mgdl < low); // stale or not
}

function isIdle() {
  const pr = state.presence;
  return !!pr && pr.mode === "away" && !needsPerson();
}

function updateIdle() {
  const el = $("idle");
  if (isIdle()) {
    if (wakeTimer) { clearTimeout(wakeTimer); wakeTimer = null; clearFlood(); } // left again mid-wake
    el.classList.remove("waking", "instant");
    el.classList.add("on");
    idleShown = true;
    return;
  }
  if (needsPerson() && (idleShown || wakeTimer)) { // alarm or low: gone now, no animation
    if (wakeTimer) { clearTimeout(wakeTimer); wakeTimer = null; }
    clearFlood(); // an alarm never waits for leaves
    el.classList.add("instant");
    el.classList.remove("on", "waking");
    idleShown = false;
    return;
  }
  if (!idleShown) return;
  idleShown = false; // someone is back: the leaves flood the screen and fall away
  el.classList.remove("instant");
  floodLeaves();
  el.classList.add("waking");
  wakeTimer = setTimeout(() => {
    wakeTimer = null;
    el.classList.remove("on", "waking");
    clearFlood();
  }, WAKE_MS);
}

// --- doctor-message takeover (R4, invariant 8) ---

// Each pending message's own sender name (GET /api/rounds/messages carries
// doctor_display_name per message; the snapshot's DoctorMessage has none).
// A read only, with the kiosk's cached PIN if there is one: it never prompts,
// and without it the paired-doctor rule names the sender.
async function loadDoctorSenders() {
  const pin = cachedPin();
  if (!pin || !state.doctorMessages.length) return;
  try {
    const res = await fetch("/api/rounds/messages", { headers: { "X-PIN": pin }, cache: "no-store" });
    if (!res.ok) return;
    const docs = await res.json();
    if (!Array.isArray(docs)) return;
    for (const d of docs) {
      const id = d && d.message && d.message.message_id;
      if (typeof id === "string" && typeof d.doctor_display_name === "string" && d.doctor_display_name.trim())
        state.doctorSenders[id] = d.doctor_display_name.trim();
    }
    renderDoctor();
  } catch {
    /* the paired-doctor rule stays */
  }
}

// The oldest pending message, echoed in plain words (doctor-echo.js). Confirm
// and Decline are fresh-PIN verbs: the keypad always opens (postFresh). The
// takeover never confirms or closes on its own: it goes away only when the
// Pi's doctor_message_resolved arrives (or the next snapshot omits it).
function renderDoctor() {
  const box = $("doctor");
  const m = state.doctorMessages[0];
  box.classList.toggle("hidden", !m);
  if (!m) return;
  const e = window.irinDoctorEcho(m, state.doctorSenders[m.message_id] || state.doctorName);
  const n = state.doctorMessages.length;
  $("doctor-count").textContent = n > 1 ? `1 of ${n} messages` : "";
  $("doctor-who").textContent = `${e.who}:`;
  $("doctor-line").textContent = e.line;
  $("doctor-units").classList.toggle("hidden", !e.insulin);
  $("doctor-units").textContent = e.insulin || "";
  $("doctor-note").classList.toggle("hidden", !e.note);
  $("doctor-note").textContent = e.note || "";
  $("doctor-confirm").textContent = e.confirm;
  $("doctor-confirm").disabled = !e.known; // never confirm what the screen could not show
  $("doctor-msg").textContent = state.doctorMsg || (e.known ? "" : "This message cannot be confirmed here. Decline it, and ask for it again.");
}

async function answerDoctor(verb) {
  const m = state.doctorMessages[0];
  if (!m) return;
  const path = `/api/rounds/messages/${encodeURIComponent(m.message_id)}/${verb}`;
  state.doctorAsking = m.message_id;
  let res;
  try {
    res = await postFresh(path, undefined, verb === "confirm" ? "Enter PIN to confirm" : "Enter PIN to decline");
  } finally {
    state.doctorAsking = null;
  }
  if (res.cancelled) return;
  state.doctorMsg = res.ok ? "sent — waiting for your Irin" : res.reason;
  render();
}

// --- Step Watch strip (R1): where the watch is, as a glance; the app carries every input ---

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const dayLabel = (iso) => { const [, m, d] = String(iso).slice(0, 10).split("-").map(Number); return m ? `${MONTHS[m - 1]} ${d}` : ""; };
// While the snapshot does not carry plan_state (contracts.StateSnapshot has no
// such field yet: FOR CHRIS), the strip applies the Pi's own step_watch rules to
// the snapshot's active_plan on the Pi's date: the current step is the latest
// planned_start on or before today, day 1 is its planned start, the next step
// is the earliest later one. A plan_state message always wins.
function planFromActive(plan, today) {
  if (!plan || plan.status !== "active" || !today || !Array.isArray(plan.steps)) return {};
  const started = plan.steps.filter((s) => s.planned_start <= today).sort((a, b) => a.planned_start.localeCompare(b.planned_start));
  const later = plan.steps.filter((s) => s.planned_start > today).sort((a, b) => a.planned_start.localeCompare(b.planned_start));
  const cur = started[started.length - 1];
  const utc = (iso) => { const [y, m, d] = iso.slice(0, 10).split("-").map(Number); return Date.UTC(y, m - 1, d); };
  const days = (a, b) => Math.round((utc(a) - utc(b)) / 86400000);
  return { active: true, drug_label: plan.drug_label, dose_label: cur ? cur.dose_label : null,
    day_in_step: cur ? days(today, cur.planned_start) + 1 : null, next_step_on: later[0] ? later[0].planned_start : null };
}
function renderWatch() {
  const w = typeof (state.planState || {}).active === "boolean" ? state.planState : planFromActive(state.activePlan, state.piDate);
  const on = w.active === true && typeof w.dose_label === "string";
  $("watch-strip").classList.toggle("hidden", !on);
  if (!on) return;
  const parts = [`Step Watch · ${w.drug_label || ""} ${w.dose_label}`.replace(/\s+/g, " ")];
  if (typeof w.day_in_step === "number") parts.push(`day ${w.day_in_step}`);
  if (w.next_step_on) parts.push(`next step ${dayLabel(w.next_step_on)}`);
  $("watch-strip").textContent = parts.join(" · ");
}

// --- Share with my doctor (R1): the QR screen ---

// POST /api/pair/start (PIN from the keypad) answers with the QR URL; only
// the screen that started a pairing can draw its QR (pairing_state never
// carries the token). The Pi learns that the doctor's browser joined only
// when asked (GET /api/pair/status), so this screen polls while its QR is
// up, with the PIN typed for Start, held in memory for this one pairing
// (never the cached ack PIN, never stored). Confirm is a fresh-PIN verb: the
// keypad opens every time (postFresh). Nothing is shared until Confirm.
const PAIR_POLL_MS = 3000;
const pair = { open: false, qr: null, pin: null, deadline: 0, msg: "", timer: null, busy: false };

function setPairing(ps) {
  const before = state.pairing.status;
  state.pairing = ps && typeof ps === "object" ? ps : {};
  state.doctorName = window.irinDoctorName(state.pairing);
  const now = state.pairing.status;
  if (now === "awaiting_confirm" && before !== "awaiting_confirm") pair.open = true; // the doctor joined: show the code here too
  if (now !== "awaiting_scan" && now !== "awaiting_confirm" && pair.qr) endQr(""); // confirmed, expired, or the mode changed
}

function endQr(msg) {
  clearInterval(pair.timer);
  pair.timer = null;
  pair.qr = null;
  pair.pin = null;
  pair.msg = msg;
}

async function pollPair() {
  if (!pair.pin) return;
  if (Date.now() > pair.deadline) {
    endQr("The code expired. Start again when the doctor is ready.");
    return render();
  }
  try {
    const res = await fetch("/api/pair/status", { headers: { "X-PIN": pair.pin }, cache: "no-store" });
    if (res.status === 401) endQr("PIN not accepted");
    else if (res.ok) setPairing(await res.json());
  } catch { /* the next poll tries again */ }
  render();
}

async function startPairing() {
  if (pair.busy) return;
  const pin = await promptPin("Enter PIN to share with your doctor");
  if (!pin) return;
  pair.busy = true;
  pair.msg = "starting…";
  render();
  try {
    const res = await fetch("/api/pair/start", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-PIN": pin },
      body: JSON.stringify({ peer_kind: "doctor" }),
    });
    if (res.status === 401) { pair.msg = "PIN not accepted"; return; }
    if (!res.ok) {
      const b = await res.json().catch(() => null);
      pair.msg = `could not start: ${(b && typeof b.detail === "string" && b.detail) || `the device refused (${res.status})`}`;
      return;
    }
    const b = await res.json();
    endQr("");
    pair.qr = { url: b.qr_url, demo: !!b.is_demo };
    pair.pin = pin;
    pair.deadline = Date.now() + (Number(b.expires_in_s) || 600) * 1000;
    pair.timer = setInterval(pollPair, PAIR_POLL_MS);
    pollPair();
  } catch {
    pair.msg = "could not reach the device";
  } finally {
    pair.busy = false;
    render();
  }
}

async function confirmPairing() {
  if (pair.busy) return;
  pair.busy = true;
  pair.msg = "confirming…"; // shown once the keypad closes: the Pi asks the relay before it answers
  render();
  try {
    const res = await postFresh("/api/pair/confirm", undefined, "Enter PIN to confirm sharing");
    if (res.cancelled) { pair.msg = ""; return; }
    pair.msg = res.ok ? `Now sharing with ${(res.value && res.value.doctor_display_name) || "your doctor"}` : res.reason;
  } finally {
    pair.busy = false;
    render();
  }
}

// The QR as SVG: one path, a dark square per module, 4-module quiet zone.
function qrSvg(text) {
  const q = window.qrcode(0, "M");
  q.addData(text);
  q.make();
  const n = q.getModuleCount(), m = 4;
  let d = "";
  for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) if (q.isDark(r, c)) d += `M${c + m} ${r + m}h1v1h-1z`;
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${n + 2 * m} ${n + 2 * m}`);
  svg.setAttribute("shape-rendering", "crispEdges");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "pairing QR code");
  const path = document.createElementNS(ns, "path");
  path.setAttribute("d", d);
  path.setAttribute("fill", "#000");
  svg.append(path);
  return svg;
}

let pairQrDrawn = null; // the URL the SVG on screen encodes
function renderPair() {
  $("pair").classList.toggle("hidden", !pair.open);
  if (!pair.open) return;
  const ps = state.pairing || {};
  const status = ps.status || "idle";
  const sharing = (Array.isArray(ps.pairings) ? ps.pairings : []).filter((p) => p && p.status === "paired");
  $("pair-list").textContent = sharing.length
    ? "Sharing with " + sharing.map((p) => p.doctor_display_name + (p.is_demo ? " (DEMO)" : "")).join(", ")
    : "Not sharing with anyone yet";

  const showQr = status === "awaiting_scan" && !!pair.qr;
  $("pair-qr").classList.toggle("hidden", !showQr);
  if (showQr && pairQrDrawn !== pair.qr.url) {
    $("pair-qr").replaceChildren(qrSvg(pair.qr.url));
    pairQrDrawn = pair.qr.url;
  }
  if (!showQr) { $("pair-qr").replaceChildren(); pairQrDrawn = null; }

  const joined = status === "awaiting_confirm" && typeof ps.code4 === "string";
  $("pair-code").classList.toggle("hidden", !joined);
  $("pair-code").textContent = joined ? ps.code4 : "";
  $("pair-confirm").classList.toggle("hidden", !joined);
  $("pair-start").classList.toggle("hidden", status !== "idle");
  $("pair-start").disabled = $("pair-confirm").disabled = pair.busy;

  let text = "";
  if (joined) {
    const who = ps.doctor_display_name || "The doctor";
    text = `${who}'s screen should show this same code. Confirm only if it matches: nothing is shared until you do.`;
  } else if (showQr) {
    const left = Math.max(0, Math.round((pair.deadline - Date.now()) / 1000));
    text = `Scan with the doctor's phone or computer. The code works once, for ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")} more.`;
    if (pair.qr.demo) text += " Demo pairing: it receives demo cards only.";
  } else if (status === "awaiting_scan") {
    text = "A pairing was started in the app: scan the code shown on the phone.";
  }
  $("pair-text").textContent = text;
  $("pair-msg").textContent = pair.msg;
}

// --- morning (step 2 numbers, from the step 11 report) ---

const RING_C = 2 * Math.PI * 50; // the ring circles' r = 50

// Only last night's report, and only one whose DEMO flag matches the current
// source: a replayed night is never shown as live (invariant 1), and an old
// night is never shown as last night.
function morningReport() {
  const rep = state.report;
  if (!rep || !rep.stats || rep.night_date !== state.piDate) return null;
  if (!!rep.is_demo !== (state.mode === "replay")) return null;
  return rep.stats;
}

function setStat(id, value, sub) {
  const el = $(id);
  el.textContent = value == null ? "—" : String(value);
  if (value != null && sub) {
    const small = document.createElement("small");
    small.textContent = sub;
    el.appendChild(small);
  }
}

function renderMorning() {
  const s = morningReport();
  const pct = (v) => (v == null ? null : `${Number(v.toFixed(1))}%`); // matches the report's narrative
  $("tir-night").textContent = s ? (pct(s.tir_pct) ?? "—") : "—";
  setStat("night-low", s ? s.low_mgdl : null, s && s.low_at ? `at ${s.low_at}` : "");
  setStat("night-high", s ? s.high_mgdl : null, s && s.high_at ? `at ${s.high_at}` : "");
  setStat("night-below", s ? pct(s.tbr_pct) : null, s && s.minutes_below_70 ? `${s.minutes_below_70} min` : "");
  setStat("night-above", s ? pct(s.tar_pct) : null, "");
  // ring: below (red) from the top, then in range (green), then above (amber)
  let start = 0;
  for (const [id, v] of [["ring-below", s?.tbr_pct], ["ring-in", s?.tir_pct], ["ring-above", s?.tar_pct]]) {
    const len = s && v != null ? (v / 100) * RING_C : 0;
    $(id).style.strokeDasharray = `${len} ${RING_C}`;
    $(id).style.strokeDashoffset = String(-start);
    start += len;
  }
  let note = "no report for last night yet";
  if (s) note = s.coverage_pct != null && s.coverage_pct < 85
    ? `sensor covered ${pct(s.coverage_pct)} of the night; some of it is missing`
    : "";
  $("morning-note").textContent = note;
  renderFamily();
}

// "Sent to Mom" (F2): shown as sent only when the Pi says status "sent";
// passive chips, the pause and approve buttons live in the phone app.
function renderFamily() {
  const box = $("morning-family");
  box.replaceChildren();
  const recips = (state.settings && state.settings.family_recipients) || [];
  const nameOf = (id) => (recips.find((r) => r.recipient_id === id) || {}).name || "family";
  for (const s of state.familyStories) {
    const chip = document.createElement("span");
    const name = nameOf(s.recipient_id);
    if (s.status === "sent") { chip.className = "fam-chip fam-sent"; chip.textContent = `Sent to ${name}`; }
    else if (s.status === "demo") {
      chip.className = "fam-chip fam-demo";
      const b = document.createElement("b"); b.textContent = "DEMO";
      chip.append(b, `${name}: not sent`);
    }
    else if (s.status === "pending_approval") { chip.className = "fam-chip fam-wait"; chip.textContent = `${name}: waiting for your approval in the app`; }
    else if (s.status === "skipped") { chip.className = "fam-chip fam-other"; chip.textContent = `${name}: skipped`; }
    else { chip.className = "fam-chip fam-other"; chip.textContent = `${name}: not sent`; }
    box.appendChild(chip);
  }
}

// --- alarm (step 3) ---

function renderAlarm(num, arrow) {
  const a = state.alarm || {};
  const low = LOW_TRIGGERS.includes(a.trigger_type);
  const sounding = low && SOUNDING.includes(a.state);
  const full = a.trigger_type === "actual_low";
  const box = $("alarm");
  box.classList.toggle("hidden", !sounding);
  document.body.classList.toggle("alarm-on", sounding);
  box.classList.toggle("tier-warning", sounding && !full);
  box.classList.toggle("tier-full", sounding && full);
  // Warning escalates when it goes Pending -> Active unacknowledged; full strobes once re-armed.
  box.classList.toggle("escalated", sounding && !full && a.state !== "pending");
  box.classList.toggle("strobe", sounding && full && a.state === "rearmed");
  if (sounding) {
    $("alarm-title").textContent = full ? "LOW" : "LOW COMING";
    $("alarm-glucose").textContent = num;
    $("alarm-trend").textContent = arrow;
    $("alarm-sub").textContent = full
      ? (a.state === "rearmed" ? "Still Low — Treat Now" : "Treat Now")
      : "predicted low within 30 minutes";
    $("alarm-ack-msg").textContent = state.ackMsg;
  }
  $("flag-acked").classList.toggle("hidden", !(low && a.state === "acknowledged"));
  const high = a.trigger_type === "high" && a.state !== "idle";   // one-shot: tint + indicator, no ack UI
  $("flag-high").classList.toggle("hidden", !high);
  document.body.classList.toggle("alarm-high", high);
}

function cachedPin() {
  try { return localStorage.getItem(PIN_KEY); } catch { return null; }
}
function storePin(pin) {
  try { pin ? localStorage.setItem(PIN_KEY, pin) : localStorage.removeItem(PIN_KEY); } catch { /* kiosk only */ }
}

// The one place the acknowledge is sent: POST /api/acknowledge, source device
// (the R2 recorder counts which screen answered). The takeover clears only
// when the Pi's alarm_state_change arrives, never on the tap itself.
async function sendAcknowledge(pin) {
  try {
    const res = await fetch("/api/acknowledge", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-PIN": pin },
      body: JSON.stringify({ source: "device" }),
    });
    if (res.status === 401) return { ok: false, badPin: true, reason: "PIN not accepted — tap Acknowledge to try again" };
    if (!res.ok) return { ok: false, reason: `the device refused (${res.status})` };
    return { ok: true };
  } catch {
    return { ok: false, reason: "could not reach the device" };
  }
}

async function onAckTap() {
  const pin = cachedPin() ?? await promptPin();
  if (!pin) return;
  storePin(pin);
  state.ackMsg = "sending…";
  render();
  const res = await sendAcknowledge(pin);
  if (res.badPin) storePin(null); // the next tap re-prompts the keypad
  state.ackMsg = res.ok ? "" : res.reason;
  render();
}

// --- fresh-PIN verbs (invariant 12) ---

// The list comes from the Pi (contracts.FRESH_PIN_ENDPOINTS). Route templates
// like /api/rounds/messages/{message_id}/confirm match one path segment per {}.
let freshPatterns = null;
const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const templateToRegExp = (t) => new RegExp("^" + t.split(/\{[^}]+\}/).map(escapeRe).join("[^/]+") + "$");
async function loadFreshPins() {
  if (freshPatterns) return freshPatterns;
  const res = await fetch("/api/contracts/fresh_pin", { cache: "no-store" });
  if (!res.ok) throw new Error(`fresh-PIN list unavailable (${res.status})`);
  const { endpoints } = await res.json();
  freshPatterns = (endpoints || []).map(templateToRegExp);
  return freshPatterns;
}
async function isFreshPath(path) {
  return (await loadFreshPins()).some((re) => re.test(path));
}

// A high-stakes verb from the kiosk: the keypad ALWAYS opens, the cached ack
// PIN is never used, and what is typed is never stored. Refuses (without
// sending) a path that is not on the fresh list, so no caller can use it to
// skip the cache rules the other way.
async function postFresh(path, body, title = "Enter PIN to confirm") {
  if (!(await isFreshPath(path))) throw new Error(`${path} is not a fresh-PIN endpoint`);
  const pin = await promptPin(title);
  if (!pin) return { ok: false, cancelled: true };
  try {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-PIN": pin },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (res.status === 401) return { ok: false, badPin: true, reason: "PIN not accepted" };
    if (!res.ok) {
      const b = await res.json().catch(() => null);
      return { ok: false, status: res.status, reason: (b && typeof b.detail === "string" && b.detail) || `the device refused (${res.status})` };
    }
    return { ok: true, value: await res.json().catch(() => null) };
  } catch {
    return { ok: false, reason: "could not reach the device" };
  }
}

// Touch keypad. Resolves with the digits, or null on cancel.
let keypadDone = null;
/** Close an open keypad as if cancelled (its question no longer exists). */
function cancelKeypad() {
  if (keypadDone) keypadDone(null);
}
function promptPin(title = "Enter PIN") {
  return new Promise((resolve) => {
    let digits = "";
    const pad = $("keypad"), keys = $("keypad-keys"), dots = $("keypad-dots");
    const show = () => { dots.textContent = "•".repeat(digits.length); };
    const done = (v) => { keypadDone = null; pad.classList.add("hidden"); keys.replaceChildren(); resolve(v); };
    keypadDone = done;
    $("keypad-title").textContent = title;
    keys.replaceChildren();
    for (const k of ["1", "2", "3", "4", "5", "6", "7", "8", "9", "✕", "0", "OK"]) {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = k;
      b.onclick = () => {
        if (k === "✕") return digits ? (digits = digits.slice(0, -1), show()) : done(null);
        if (k === "OK") return digits && done(digits);
        if (digits.length < 8) { digits += k; show(); }
      };
      keys.append(b);
    }
    show();
    pad.classList.remove("hidden");
  });
}

function drawGraph() {
  const canvas = $("graph");
  const dpr = window.devicePixelRatio || 1;
  const W = canvas.clientWidth, H = canvas.clientHeight;
  if (!W || !H) return;
  if (canvas.width !== Math.round(W * dpr) || canvas.height !== Math.round(H * dpr)) {
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);

  const pts = state.points;
  const r = state.latest;
  const dim = r && (r.is_stale || isDisconnected()); // old data is drawn grey, never as live
  const fs = Math.max(11, Math.round(H * 0.055));
  const padL = fs * 2.6, padR = fs * 0.8, padT = fs * 0.6, padB = fs * 1.8;
  const plotW = W - padL - padR, plotH = H - padT - padB;

  // Forecast only on fresh data (invariant 1: never forecast on stale or while cut off).
  const fc = state.forecast && r && !dim ? state.forecast : null;
  const horizonMin = fc ? fc.horizon_min : 30;
  const tEnd = r ? toMs(r.timestamp) + horizonMin * 60000 : 0;
  const tStart = tEnd - (WINDOW_MIN + horizonMin) * 60000;

  let yMax = 300;
  for (const p of pts) yMax = Math.max(yMax, p.mgdl + 20);
  if (fc) yMax = Math.max(yMax, fc.predicted_mgdl + 20);
  const yMin = 40;
  const x = (t) => padL + ((t - tStart) / (tEnd - tStart)) * plotW;
  const y = (v) => padT + (1 - (Math.min(Math.max(v, yMin), yMax) - yMin) / (yMax - yMin)) * plotH;

  // target band
  ctx.fillStyle = "rgba(125, 155, 110, 0.20)";  // target band: brand Sage
  ctx.fillRect(padL, y(TARGET_HIGH), plotW, y(TARGET_LOW) - y(TARGET_HIGH));

  // y gridlines
  ctx.font = `${fs}px system-ui, sans-serif`;
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";
  for (const v of [TARGET_LOW, TARGET_HIGH, 250].filter((v) => v < yMax)) {
    ctx.strokeStyle = v === TARGET_LOW ? "rgba(255,59,48,0.5)" : "#4A4C41";
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(padL, y(v)); ctx.lineTo(padL + plotW, y(v)); ctx.stroke();
    ctx.fillStyle = "#A9AA9E";
    ctx.fillText(String(v), padL - fs * 0.4, y(v));
  }

  if (!r) {
    ctx.textAlign = "center";
    ctx.fillStyle = "#8C8D82";
    ctx.fillText("waiting for readings", padL + plotW / 2, padT + plotH / 2);
    return;
  }

  // x labels: every hour, Pi clock
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  ctx.fillStyle = "#A9AA9E";
  const hour = 3600000;
  for (let t = Math.ceil(tStart / hour) * hour; t <= tEnd; t += hour) {
    ctx.strokeStyle = "#3A3B33";
    ctx.beginPath(); ctx.moveTo(x(t), padT); ctx.lineTo(x(t), padT + plotH); ctx.stroke();
    ctx.fillText(hour12(t), x(t), padT + plotH + fs * 0.4);
  }

  // "now" divider between history and forecast
  const tNow = toMs(r.timestamp);
  ctx.strokeStyle = "#5C5E52";
  ctx.setLineDash([2, 4]);
  ctx.beginPath(); ctx.moveTo(x(tNow), padT); ctx.lineTo(x(tNow), padT + plotH); ctx.stroke();
  ctx.setLineDash([]);

  // history line, broken across gaps, no point markers (George, stepping in for
  // Justin): the line itself is colored by range, white in 70-180, YELLOW above,
  // RED below, switching exactly where it crosses 70 or 180 (each segment is
  // split at the crossing, so a colour never bleeds past the threshold). Stale
  // or disconnected data stays all grey (invariant 1: never drawn as live).
  ctx.lineWidth = Math.max(2, fs * 0.18);
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  const rangeColor = (v) => (dim ? "#777" : v < TARGET_LOW ? LINE_LOW : v > TARGET_HIGH ? LINE_HIGH : LINE_IN);
  const piece = (t0, v0, t1, v1) => {
    ctx.strokeStyle = rangeColor((v0 + v1) / 2);
    ctx.beginPath(); ctx.moveTo(x(t0), y(v0)); ctx.lineTo(x(t1), y(v1)); ctx.stroke();
  };
  let prev = null;
  for (const p of pts) {
    if (prev && p.t - prev.t <= GAP_MIN * 60000) {
      // the times (in order) where this segment crosses 70 or 180
      const cuts = [TARGET_LOW, TARGET_HIGH]
        .filter((th) => (prev.mgdl - th) * (p.mgdl - th) < 0)
        .map((th) => prev.t + ((th - prev.mgdl) / (p.mgdl - prev.mgdl)) * (p.t - prev.t))
        .sort((a, b) => a - b);
      let t0 = prev.t, v0 = prev.mgdl;
      for (const tc of cuts) {
        const vc = prev.mgdl + ((tc - prev.t) / (p.t - prev.t)) * (p.mgdl - prev.mgdl);
        piece(t0, v0, tc, vc);
        t0 = tc; v0 = vc;
      }
      piece(t0, v0, p.t, p.mgdl);
    } else {
      // the first reading of a run (after a gap, or the very first) gets a cap
      // in its range colour, so a lone reading between two gaps still shows
      ctx.strokeStyle = rangeColor(p.mgdl);
      ctx.beginPath(); ctx.moveTo(x(p.t), y(p.mgdl)); ctx.lineTo(x(p.t) + 0.1, y(p.mgdl)); ctx.stroke();
    }
    prev = p;
  }
  ctx.lineCap = "butt";

  // DOTTED forecast: latest reading -> predicted value at +horizon
  if (fc) {
    const ft = toMs(fc.timestamp) + fc.horizon_min * 60000;
    ctx.strokeStyle = PREDICTION_COLOR;  // the same colour as the prediction text under the number
    ctx.lineWidth = Math.max(2, fs * 0.18);
    ctx.lineCap = "round";
    ctx.setLineDash([0.1, fs * 0.55]);
    ctx.beginPath(); ctx.moveTo(x(tNow), y(r.glucose_mgdl)); ctx.lineTo(x(ft), y(fc.predicted_mgdl)); ctx.stroke();
    ctx.setLineDash([]);
    ctx.lineCap = "butt";
  }
  // No line: say why ("the UI says so"), never leave the gap unexplained.
  if (!fc && state.forecastNote && !dim) {
    ctx.textAlign = "right";
    ctx.textBaseline = "top";
    ctx.fillStyle = "#A9AA9E";
    ctx.font = `${Math.round(fs * 0.85)}px system-ui, sans-serif`;
    ctx.fillText(state.forecastNote, padL + plotW, padT);
  }
}

// The disconnected banner is time-based, so re-check it even with no messages.
setInterval(() => { if (!state.connected) render(); }, 1000);
window.addEventListener("resize", drawGraph);
$("alarm-ack").addEventListener("click", onAckTap);
$("doctor-confirm").addEventListener("click", () => answerDoctor("confirm"));
$("doctor-decline").addEventListener("click", () => answerDoctor("decline"));
$("share-open").addEventListener("click", () => { pair.open = true; pair.msg = ""; render(); });
$("pair-close").addEventListener("click", () => { pair.open = false; endQr(""); render(); });
$("pair-start").addEventListener("click", startPairing);
$("pair-confirm").addEventListener("click", confirmPairing);
// the QR countdown ticks without a message arriving
setInterval(() => { if (pair.qr) render(); }, 1000);
render();
connect();
pollDevice();
setInterval(pollDevice, POLL_MS);
