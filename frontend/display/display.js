// Kiosk display: detail screen (justin.md step 1) and the Detail / Night /
// Morning modes (step 2). The mode comes from the Pi's clock (/api/health)
// and settings.night_window_*, never the browser's clock.
// Alarm visuals (step 3): the tier comes from alarm.trigger_type, the
// intensity from alarm.state; the takeover covers every mode.
// Renders ONLY from the state_snapshot sent on every WebSocket connect plus the
// updates after it (hard client rule 1); never assumes it saw messages while
// disconnected. Time on the graph is the Pi's clock (reading timestamps),
// never the browser's.
// Fresh-PIN verbs (later steps) read their list from GET /api/contracts/fresh_pin,
// never a hand copy.
const $ = (id) => document.getElementById(id);

const WINDOW_MIN = 180;        // graph history span
const GAP_MIN = 15;            // don't join points across a gap this long (matches STALE_AFTER_MIN)
const DISCONNECT_BANNER_MS = 15000;
const TARGET_LOW = 70;         // time-in-range band, consensus 70-180 mg/dL
const TARGET_HIGH = 180;
const MORNING_MIN = 120;       // Morning screen lasts 2 h after the night window ends
const HEALTH_POLL_MS = 5000;   // the Pi's clock, polled (at 60x replay: 5 clock-min)

const TREND_ARROWS = {
  DoubleUp: "⇈", SingleUp: "↑", FortyFiveUp: "↗", Flat: "→",
  FortyFiveDown: "↘", SingleDown: "↓", DoubleDown: "⇊",
};

const state = {
  mode: null,
  latest: null,        // Reading
  forecast: null,      // Forecast
  settings: null,
  clockSynced: true,
  points: [],          // [{t: ms, mgdl}] oldest first, readings received this session
  connected: false,
  downSince: Date.now(),
  piClock: null,       // ms, the Pi's clock.now() from /api/health
  alarm: { state: "idle", trigger_type: null },  // AlarmState
  ackMsg: "",
};

const PIN_KEY = "irin.kiosk_pin"; // kiosk-only concession: the low-stakes ack PIN lives in localStorage
const LOW_TRIGGERS = ["predicted_low", "actual_low"];
const SOUNDING = ["pending", "active", "rearmed"]; // pending = the predicted-low warning

const toMs = (iso) => new Date(iso).getTime(); // naive Pi-local timestamps; only differences matter
const hhmm = (ms) => new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });

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

function onMessage(msg) {
  const p = msg.payload || {};
  switch (msg.type) {
    case "state_snapshot":
      setMode(p.mode);
      state.settings = p.settings || null;
      state.clockSynced = p.clock_synced !== false;
      state.forecast = p.forecast || null;
      state.alarm = p.alarm || { state: "idle", trigger_type: null };
      if (p.latest_reading) addReading(p.latest_reading);
      else state.latest = null;
      break;
    case "reading_update":
      addReading(p);
      break;
    case "forecast_update":
      state.forecast = p;
      break;
    case "mode_change":
      setMode(p.mode);
      break;
    case "alarm_state_change":
      state.alarm = p.alarm || p;
      if (!SOUNDING.includes(state.alarm.state)) state.ackMsg = "";
      break;
    case "settings_change":
      state.settings = p.settings || p;
      break;
    default:
      return; // other types belong to later steps; the hub's echo replies have no type
  }
  render();
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

async function pollHealth() {
  try {
    const h = await (await fetch("/api/health", { cache: "no-store" })).json();
    const t = toMs(h.clock);
    if (!Number.isNaN(t)) state.piClock = t;
  } catch { /* the disconnected banner covers a dead backend */ }
  render();
}

const minutesOfDay = (ms) => { const d = new Date(ms); return d.getHours() * 60 + d.getMinutes(); };
const parseHHMM = (s) => { const [h, m] = String(s || "").split(":").map(Number); return h * 60 + (m || 0); };
// true when m lies in [start, end) on a 24 h circle
const inWindow = (m, start, end) => start <= end ? m >= start && m < end : m >= start || m < end;

// "detail" | "night" | "morning". Detail whenever the Pi's clock is unknown or
// not yet NTP-synced: no clock, no night decision.
function screenMode() {
  const now = state.piClock ?? (state.latest ? toMs(state.latest.timestamp) : null);
  if (now === null || !state.clockSynced || !state.settings) return "detail";
  const start = parseHHMM(state.settings.night_window_start);
  const end = parseHHMM(state.settings.night_window_end);
  if (Number.isNaN(start) || Number.isNaN(end)) return "detail";
  const m = minutesOfDay(now);
  if (inWindow(m, start, end)) return "night";
  if (inWindow(m, end, (end + MORNING_MIN) % 1440)) return "morning";
  return "detail";
}

// --- render ---

const isDisconnected = () => !state.connected && Date.now() - state.downSince > DISCONNECT_BANNER_MS;

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
  document.body.classList.toggle("is-stale", stale);
  document.body.classList.toggle("is-disconnected", disconnected);

  const mode = screenMode();
  for (const m of ["detail", "night", "morning"]) {
    document.body.classList.toggle(`mode-${m}`, m === mode);
    $(m).classList.toggle("hidden", m !== mode);
  }
  const num = r ? String(Math.round(r.glucose_mgdl)) : "---";
  const arrow = r && !r.is_stale ? (TREND_ARROWS[r.trend] ?? "?") : "";
  $("night-glucose").textContent = $("morning-glucose").textContent = num;
  $("night-trend").textContent = $("morning-trend").textContent = arrow;
  const now = state.piClock ?? (r ? toMs(r.timestamp) : null);
  $("night-clock").textContent = now === null ? "--:--" : hhmm(now);
  // The Morning numbers (overnight low/high with times, time below/above,
  // TIR) are night metrics from the backend (nights.py); the snapshot has no
  // field for them yet, so the screen shows "—" until it does (journal request).

  if (r) {
    $("glucose").textContent = Math.round(r.glucose_mgdl);
    $("trend").textContent = r.is_stale ? "" : (TREND_ARROWS[r.trend] ?? "?");
    $("reading-time").textContent = `reading at ${hhmm(toMs(r.timestamp))}`;
  } else {
    $("glucose").textContent = "---";
    $("trend").textContent = "";
    $("reading-time").textContent = state.connected ? "no reading yet" : "connecting to device…";
  }
  // IOB, last dose, and today's TIR are not in the snapshot yet (contracts
  // request to Chris, see journal); the tiles stay "—" until they are.
  renderAlarm(num, arrow);
  if (mode === "detail") drawGraph();
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
      ? (a.state === "rearmed" ? "still low — treat now" : "treat now")
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

// The one place the acknowledge is sent (ack_source = device). The backend's
// acknowledge endpoint / WS command shape is not in contracts.py or main.py
// yet (chris.md step 8), so nothing is sent; wire it here when it lands, and
// on a 401 call storePin(null) and re-prompt the keypad.
async function sendAcknowledge(_pin) {
  return { ok: false, reason: "not connected yet: the device has no acknowledge endpoint" };
}

async function onAckTap() {
  const pin = cachedPin() ?? await promptPin();
  if (!pin) return;
  storePin(pin);
  state.ackMsg = "sending…";
  render();
  const res = await sendAcknowledge(pin);
  state.ackMsg = res.ok ? "" : res.reason;
  render();
}

// Touch keypad. Resolves with the digits, or null on cancel.
function promptPin() {
  return new Promise((resolve) => {
    let digits = "";
    const pad = $("keypad"), keys = $("keypad-keys"), dots = $("keypad-dots");
    const show = () => { dots.textContent = "•".repeat(digits.length); };
    const done = (v) => { pad.classList.add("hidden"); keys.replaceChildren(); resolve(v); };
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
  ctx.fillStyle = "rgba(60, 180, 110, 0.14)";
  ctx.fillRect(padL, y(TARGET_HIGH), plotW, y(TARGET_LOW) - y(TARGET_HIGH));

  // y gridlines
  ctx.font = `${fs}px system-ui, sans-serif`;
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";
  for (const v of [TARGET_LOW, TARGET_HIGH, 250].filter((v) => v < yMax)) {
    ctx.strokeStyle = v === TARGET_LOW ? "rgba(255,59,48,0.5)" : "#262626";
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(padL, y(v)); ctx.lineTo(padL + plotW, y(v)); ctx.stroke();
    ctx.fillStyle = "#777";
    ctx.fillText(String(v), padL - fs * 0.4, y(v));
  }

  if (!r) {
    ctx.textAlign = "center";
    ctx.fillStyle = "#555";
    ctx.fillText("waiting for readings", padL + plotW / 2, padT + plotH / 2);
    return;
  }

  // x labels: every hour, Pi clock
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  ctx.fillStyle = "#777";
  const hour = 3600000;
  for (let t = Math.ceil(tStart / hour) * hour; t <= tEnd; t += hour) {
    ctx.strokeStyle = "#1a1a1a";
    ctx.beginPath(); ctx.moveTo(x(t), padT); ctx.lineTo(x(t), padT + plotH); ctx.stroke();
    ctx.fillText(hhmm(t), x(t), padT + plotH + fs * 0.4);
  }

  // "now" divider between history and forecast
  const tNow = toMs(r.timestamp);
  ctx.strokeStyle = "#333";
  ctx.setLineDash([2, 4]);
  ctx.beginPath(); ctx.moveTo(x(tNow), padT); ctx.lineTo(x(tNow), padT + plotH); ctx.stroke();
  ctx.setLineDash([]);

  // history line, broken across gaps
  const lineColor = dim ? "#777" : "#e8e8e8";
  ctx.strokeStyle = lineColor;
  ctx.lineWidth = Math.max(2, fs * 0.18);
  ctx.lineJoin = "round";
  ctx.beginPath();
  let prev = null;
  for (const p of pts) {
    if (!prev || p.t - prev.t > GAP_MIN * 60000) ctx.moveTo(x(p.t), y(p.mgdl));
    else ctx.lineTo(x(p.t), y(p.mgdl));
    prev = p;
  }
  ctx.stroke();

  // points colored by range
  const dot = Math.max(2.5, fs * 0.22);
  for (const p of pts) {
    ctx.fillStyle = dim ? "#777"
      : p.mgdl < TARGET_LOW ? "#ff3b30"
      : p.mgdl > TARGET_HIGH ? "#ffb000" : "#e8e8e8";
    ctx.beginPath(); ctx.arc(x(p.t), y(p.mgdl), dot, 0, Math.PI * 2); ctx.fill();
  }

  // DOTTED forecast: latest reading -> predicted value at +horizon
  if (fc) {
    const ft = toMs(fc.timestamp) + fc.horizon_min * 60000;
    ctx.strokeStyle = fc.predicted_mgdl < TARGET_LOW ? "#ff3b30" : "#9ab";
    ctx.lineWidth = Math.max(2, fs * 0.18);
    ctx.lineCap = "round";
    ctx.setLineDash([0.1, fs * 0.55]);
    ctx.beginPath(); ctx.moveTo(x(tNow), y(r.glucose_mgdl)); ctx.lineTo(x(ft), y(fc.predicted_mgdl)); ctx.stroke();
    ctx.setLineDash([]);
    ctx.lineCap = "butt";
  }
}

// The disconnected banner is time-based, so re-check it even with no messages.
setInterval(() => { if (!state.connected) render(); }, 1000);
window.addEventListener("resize", drawGraph);
$("alarm-ack").addEventListener("click", onAckTap);
render();
connect();
pollHealth();
setInterval(pollHealth, HEALTH_POLL_MS);
