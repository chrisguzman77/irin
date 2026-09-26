// Kiosk display, day-one placeholder: polls /api/latest every 5 s.
// WebSocket wiring: Chris (state_snapshot + updates, the hard client rule).
// Fresh-PIN verbs read their list from GET /api/contracts/fresh_pin, never a hand copy.
const $ = (id) => document.getElementById(id);

async function poll() {
  try {
    const [h, r] = await Promise.all([fetch("/api/health"), fetch("/api/latest")]);
    const health = await h.json();
    $("badge").classList.toggle("hidden", health.datasource !== "replay");
    if (!r.ok) { $("meta").textContent = `no reading (${r.status})`; return; }
    const reading = await r.json();
    $("glucose").textContent = Math.round(reading.glucose_mgdl);
    $("glucose").classList.toggle("stale", reading.is_stale);
    $("stale").classList.toggle("hidden", !reading.is_stale);
    $("trend").textContent = reading.trend;
    $("meta").textContent = `${reading.source} · ${reading.timestamp}`;
  } catch (e) {
    $("meta").textContent = "backend unreachable";
  }
}
poll();
setInterval(poll, 5000);
