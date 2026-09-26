// Kiosk display, detail screen (justin.md step 1).
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
};

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

// --- render ---

const isDisconnected = () => !state.connected && Date.now() - state.downSince > DISCONNECT_BANNER_MS;

function render() {
  const r = state.latest;
  const stale = !r || r.is_stale;
  const disconnected = isDisconnected();

  $("badge-demo").classList.toggle("hidden", state.mode !== "replay");
  $("badge-clock").classList.toggle("hidden", state.clockSynced);
  $("banner-stale").classList.toggle("hidden", !(r && r.is_stale));
  $("banner-disconnected").classList.toggle("hidden", !disconnected);
  document.body.classList.toggle("is-stale", stale);
  document.body.classList.toggle("is-disconnected", disconnected);

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
  drawGraph();
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
render();
connect();
