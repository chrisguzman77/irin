// justin.md R5 (last, optional): the night replay. Tap a night on a card and the
// card's OWN nights list plays like game film, about 20 s: a playhead walks the
// window night by night, each night lands on a low-point chart (the 70 mg/dL
// line, a dashed ring under 85% coverage, the rise in the caption only),
// and a caption reads that night's own fields; the film slows and holds on the
// night that was tapped. It draws only numbers the card carries (invariant 7):
// a card holds per-night summaries, never readings, so there is no curve to
// invent, and a night without a low point is drawn as "no low point on this card".
const $ = (id) => document.getElementById(id);
const NS = "http://www.w3.org/2000/svg";
const svg = (tag, attrs = {}, text) => {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, String(v));
  if (text !== undefined) e.textContent = text;
  return e;
};
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
};

const TOTAL_MS = 20000;
const HOLD_MS = 4000; // on the tapped night
const REASON = {
  clean: "clean", late_meal: "late meal", late_correction: "late correction", basal_late: "basal late",
  basal_missed: "basal missed", exercise: "exercise", treated_low: "treated low", stale: "sensor gap", away: "away",
};
const COLOR = {
  clean: "#16a34a", late_meal: "#ca8a04", late_correction: "#ea580c", basal_late: "#dc2626", basal_missed: "#dc2626",
  exercise: "#2563eb", treated_low: "#b91c1c", stale: "#9ca3af", away: "#9333ea", other: "#6b7280",
};
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const day = (iso) => { const [, m, d] = String(iso).slice(0, 10).split("-").map(Number); return m ? `${MONTHS[m - 1]} ${d}` : String(iso); };
const fin = (v) => typeof v === "number" && Number.isFinite(v);
const num = (v) => (Number.isInteger(v) ? String(v) : String(Number(v.toFixed(1))));

const W = 640, H = 260, L = 44, R = 26, T = 16, B = 34;

let timer = null;
let frames = [];
let at = 0;
let nights = [];
let target = 0;

function caption(nt) {
  const codes = Array.isArray(nt.reason_codes) && nt.reason_codes.length ? nt.reason_codes : ["other"];
  const how = nt.code_source === "logged" ? "logged" : "inferred from the trace";
  const bits = [`${day(nt.night_date)}: ${codes.map((c) => REASON[c] || String(c).replace(/_/g, " ")).join(", ")} (${how})`];
  bits.push(fin(nt.low_point_mgdl) ? `low point ${num(nt.low_point_mgdl)} mg/dL` : "no low point on this card");
  if (fin(nt.rise_mgdl)) bits.push(`overnight rise ${nt.rise_mgdl > 0 ? "+" : ""}${num(nt.rise_mgdl)} mg/dL`);
  if (fin(nt.near_miss_count)) bits.push(`${num(nt.near_miss_count)} near-miss${nt.near_miss_count === 1 ? "" : "es"}`);
  if (fin(nt.coverage_pct)) bits.push(`${num(nt.coverage_pct)}% sensor coverage${nt.coverage_pct < 85 ? " (too little for a conclusion)" : ""}`);
  return bits.join(" · ");
}

function draw(upto) {
  const lows = nights.map((n) => n.low_point_mgdl).filter(fin);
  const lo = Math.min(50, ...lows) - 5, hi = Math.max(140, ...lows) + 10;
  const x = (i) => L + (nights.length === 1 ? (W - L - R) / 2 : (i * (W - L - R)) / (nights.length - 1));
  const y = (v) => T + ((hi - v) * (H - T - B)) / (hi - lo);
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, class: "film-svg", role: "img", "aria-label": "Night replay" });
  root.append(svg("rect", { x: L, y: y(70), width: W - L - R, height: Math.max(0, y(lo) - y(70)), fill: "#fee2e2" }));
  for (const g of [70, 100, 140].filter((v) => v > lo && v < hi)) {
    root.append(svg("line", { x1: L, x2: W - R, y1: y(g), y2: y(g), stroke: g === 70 ? "#dc2626" : "#e5e7eb", "stroke-dasharray": g === 70 ? "4 3" : "" }));
    root.append(svg("text", { x: L - 6, y: y(g) + 4, "text-anchor": "end", class: "film-axis" }, String(g)));
  }
  root.append(svg("text", { x: 4, y: T + 4, class: "film-axis" }, "mg/dL"));
  // the playhead
  root.append(svg("line", { x1: x(upto), x2: x(upto), y1: T, y2: H - B, stroke: "#111827", "stroke-width": 1.5, opacity: 0.35 }));
  let prev = null;
  nights.forEach((nt, i) => {
    const label = svg("text", { x: x(i), y: H - B + 16, "text-anchor": "middle", class: `film-axis${i === target ? " film-target" : ""}` }, day(nt.night_date));
    if (nights.length <= 16 || i % 2 === 0 || i === target) root.append(label);
    if (i > upto) return;
    const codes = Array.isArray(nt.reason_codes) && nt.reason_codes.length ? nt.reason_codes : ["other"];
    const color = COLOR[codes[0]] || COLOR.other;
    if (!fin(nt.low_point_mgdl)) {
      root.append(svg("text", { x: x(i), y: y(lo) - 6, "text-anchor": "middle", class: "film-axis" }, "–"));
      prev = null;
      return;
    }
    const cy = y(nt.low_point_mgdl);
    if (prev) root.append(svg("line", { x1: prev[0], y1: prev[1], x2: x(i), y2: cy, stroke: "#9ca3af", "stroke-width": 1 }));
    prev = [x(i), cy];
    const low = fin(nt.coverage_pct) && nt.coverage_pct < 85;
    root.append(svg("circle", { cx: x(i), cy, r: i === upto ? 9 : 6, fill: low ? "#fff" : color, stroke: color, "stroke-width": 2,
      "stroke-dasharray": low ? "3 2" : "" }));
    if (i === target) root.append(svg("circle", { cx: x(i), cy, r: 14, fill: "none", stroke: "#111827", "stroke-width": 1.5 }));
  });
  $("filmchart").replaceChildren(root);
  $("filmcap").textContent = caption(nights[upto]);
  $("filmclock").textContent = `Night ${upto + 1} of ${nights.length}`;
}

function play() {
  clearTimeout(timer);
  const step = () => {
    draw(frames[at].i);
    const wait = frames[at].ms;
    at++;
    if (at < frames.length) timer = setTimeout(step, wait);
    else { timer = null; $("filmplay").textContent = "Replay"; }
  };
  $("filmplay").textContent = "Pause";
  step();
}

let wired = false;
/** @param {object} card the SignalCard; @param {number} index the tapped night in card.nights */
export function openReplay(card, index) {
  nights = [...(card.nights || [])].map((n, i) => ({ ...n, _i: i }))
    .sort((a, b) => String(a.night_date).localeCompare(String(b.night_date)));
  if (!nights.length) return;
  target = Math.max(0, nights.findIndex((n) => n._i === index));
  // every night up to the tapped one, then the rest: ~20 s in all, holding on the tapped night
  const each = nights.length > 1 ? Math.max(250, Math.round((TOTAL_MS - HOLD_MS) / (nights.length - 1))) : 0;
  frames = nights.map((_, i) => ({ i, ms: i === target ? HOLD_MS : each }));
  at = 0;
  if (!wired) {
    wired = true;
    $("filmclose").addEventListener("click", () => { clearTimeout(timer); timer = null; $("film").close(); });
    $("film").addEventListener("close", () => { clearTimeout(timer); timer = null; });
    $("filmplay").addEventListener("click", () => {
      if (timer) { clearTimeout(timer); timer = null; $("filmplay").textContent = "Play"; return; }
      if (at >= frames.length) at = 0;
      play();
    });
  }
  $("filmtitle").textContent = `Night replay · ${day(nights[0].night_date)} – ${day(nights[nights.length - 1].night_date)}`;
  $("filmnote").replaceChildren(el("span", "", "Drawn only from this card's nights: each night's low point (dot), reason (colour) and coverage; the rise is in the caption. Hollow dashed = under 85% coverage."));
  $("film").showModal();
  play();
}
