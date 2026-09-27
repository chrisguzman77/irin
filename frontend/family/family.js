import { CLOUD_URL } from "./config.js";

// justin.md step 9 / A3: the read-only family view. GET {cloud}/v1/family/last_night
// with the family bearer (fields pinned in cloud/README.md), every 60 s. The page
// never talks to the Pi, so it keeps working with the Pi off, and then says stale.
// The bearer arrives in the link the patient sends: family.<domain>/#t=<token>
// (the fragment never reaches a server); it is kept in this browser and removed
// from the address bar. The patient revokes it from the app's Family section.
// A night with no data is said plainly, never told as fine.
const KEY = "irin.family.token";
const POLL_MS = 60000;
const $ = (id) => document.getElementById(id);
const ARROWS = { DoubleUp: "⇈", SingleUp: "↑", FortyFiveUp: "↗", Flat: "→", FortyFiveDown: "↘", SingleDown: "↓", DoubleDown: "⇊" };
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
// Timestamps are the Pi's naive local time: read as text, never through a timezone.
const clock = (iso) => {
  const [h, m] = String(iso).slice(11, 16).split(":").map(Number);
  return `${h % 12 === 0 ? 12 : h % 12}:${String(m).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`;
};
const day = (iso) => { const [, m, d] = String(iso).slice(0, 10).split("-").map(Number); return `${MONTHS[m - 1]} ${d}`; };
const mins = (iso) => { const [h, m] = String(iso).slice(11, 16).split(":").map(Number); return h * 60 + m; };

function token() {
  const m = location.hash.match(/(?:^#|&)t=([^&]+)/);
  if (m) {
    try { localStorage.setItem(KEY, decodeURIComponent(m[1])); } catch { /* kept for this visit only */ }
    history.replaceState(null, "", location.pathname + location.search); // drop only the token
    return decodeURIComponent(m[1]);
  }
  try { return localStorage.getItem(KEY); } catch { return null; }
}

function renderNow(c) {
  const box = $("now");
  if (!c) {
    $("mgdl").textContent = "---";
    $("trend").textContent = "";
    $("since").textContent = "No reading yet.";
    box.classList.add("stale");
    return;
  }
  box.classList.toggle("stale", !!c.stale);
  $("mgdl").textContent = Math.round(c.mgdl);
  $("trend").textContent = c.stale ? "" : (ARROWS[c.trend] || "");
  const m = Math.round(c.minutes_since);
  $("since").textContent = c.stale
    ? `Stale: no new reading for ${m} minutes (last at ${clock(c.at)}). The number may be old.`
    : `${m <= 1 ? "Just now" : `${m} minutes ago`} (${clock(c.at)})`;
}

function renderStrip(points) {
  const svg = $("strip");
  const W = 320, H = 120, top = 6, bot = 100, x0 = 4, x1 = W - 4;
  const ns = "http://www.w3.org/2000/svg";
  const el = (tag, attrs, text) => {
    const e = document.createElementNS(ns, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    if (text) e.textContent = text;
    return e;
  };
  svg.replaceChildren();
  // 22:00 -> 07:00 (540 minutes), the strip's fixed window
  const x = (iso) => { let t = mins(iso) - 22 * 60; if (t < 0) t += 1440; return x0 + (t / 540) * (x1 - x0); };
  const lo = 40, hi = Math.max(250, ...points.map((p) => p.mgdl));
  const y = (v) => bot - ((Math.min(v, hi) - lo) / (hi - lo)) * (bot - top);
  svg.append(el("rect", { x: x0, y: y(180), width: x1 - x0, height: y(70) - y(180), fill: "#e8f5ec" }));
  svg.append(el("line", { x1: x0, x2: x1, y1: y(70), y2: y(70), stroke: "#d33", "stroke-dasharray": "4 3" }));
  // break the line across gaps longer than 15 minutes: never draw through missing data
  let seg = [], prev = null;
  const flush = () => { if (seg.length > 1) svg.append(el("polyline", { points: seg.join(" "), fill: "none", stroke: "#222", "stroke-width": "2" })); seg = []; };
  for (const p of points) {
    let t = mins(p.time); if (t < 22 * 60) t += 1440;
    if (prev !== null && t - prev > 15) flush();
    seg.push(`${x(p.time)},${y(p.mgdl)}`);
    prev = t;
  }
  flush();
  for (const [label, at] of [["10 PM", "2000-01-01T22:00"], ["1 AM", "2000-01-02T01:00"], ["4 AM", "2000-01-02T04:00"], ["7 AM", "2000-01-02T07:00"]])
    svg.append(el("text", { x: x(at), y: H - 4, "font-size": "10", fill: "#777", "text-anchor": label === "10 PM" ? "start" : label === "7 AM" ? "end" : "middle" }, label));
}

function renderNight(n) {
  if (!n) {
    $("night-title").textContent = "Last night";
    $("night-empty").hidden = false;
    $("night-body").hidden = true;
    return;
  }
  $("night-empty").hidden = true;
  $("night-body").hidden = false;
  const [y, mo, d] = n.night_date.split("-").map(Number);
  const next = new Date(Date.UTC(y, mo - 1, d + 1)).toISOString().slice(0, 10); // the morning after
  $("night-title").textContent = `Night of ${day(n.night_date)} – ${day(next)}${n.in_progress ? " (so far)" : ""}`;
  $("low").textContent = n.low ? `${Math.round(n.low.mgdl)} at ${clock(n.low.at)}` : "—";
  $("high").textContent = n.high ? `${Math.round(n.high.mgdl)} at ${clock(n.high.at)}` : "—";
  $("under").textContent = `${n.minutes_below_70} min`;
  $("coverage").textContent = n.coverage_pct < 85
    ? `The sensor missed part of this night (${Math.round(n.coverage_pct)}% covered), so this picture is incomplete.`
    : `Sensor coverage ${Math.round(n.coverage_pct)}%.`;
  renderStrip(Array.isArray(n.strip) ? n.strip : []);
}

async function poll(t) {
  let res;
  try {
    res = await fetch(`${CLOUD_URL}/v1/family/last_night`, { headers: { Authorization: `Bearer ${t}` }, cache: "no-store" });
  } catch {
    $("status").textContent = "Cannot reach Irin Cloud right now. Trying again in a minute.";
    $("now").classList.add("stale");
    return true;
  }
  if (res.status === 401) {
    try { localStorage.removeItem(KEY); } catch { /* nothing kept */ }
    $("view").hidden = true;
    $("msg").textContent = "This link has been turned off by the person who shared it.";
    return false;
  }
  if (!res.ok) {
    $("status").textContent = `Irin Cloud answered ${res.status}. Trying again in a minute.`;
    return true;
  }
  const b = await res.json();
  $("demo").hidden = !b.is_demo;
  $("view").hidden = false;
  renderNow(b.current);
  renderNight(b.last_night);
  $("status").textContent = `Updated ${clock(b.as_of)}.`;
  return true;
}

const t = token();
if (!t) {
  $("nolink").hidden = false;
} else {
  const tick = async () => { if (await poll(t)) setTimeout(tick, POLL_MS); };
  tick();
}
