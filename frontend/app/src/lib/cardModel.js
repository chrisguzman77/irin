// Clinical Signal Card v0 -> what a renderer draws (relay/README.md, contracts.SignalCard).
// ONE module for both renderers: the clinician inbox imports this file, and the
// app's Rounds tab imports an IDENTICAL copy (frontend/app/src/lib/cardModel.js);
// keep the two byte-for-byte equal. Pure: no DOM, no fetch.
//
// Rules it enforces (invariants 7 and 9):
// - one metrics row per labeled metric (the card's `confidence` keys), each with
//   its confidence label; a missing value says "no data", never a blank row;
// - companion values (nights, window/baseline low point) fold into their row;
// - any other metric is still shown, marked "no label", never dropped;
// - reason codes show whether they were logged or inferred;
// - a card never gains a number the card does not carry.

const PROGRAM = { standing: "Standing", step_watch: "Step Watch" };
const KIND = {
  basal_check: "Basal Check", hypo_response: "Hypo Response", follow_up: "Follow-up",
  early_check: "Early check", step_check: "Step check", step_gate: "Step gate",
  safety: "Safety", graduation: "Graduation", baseline_note: "Baseline note",
};
const SOURCE = { irin_bedside: "Irin Bedside", irin_brain: "Irin Brain" };
const STATUS = { red: "Red", amber: "Amber", green: "Green", insufficient: "Insufficient data" };
const FLAG = {
  lows: "lows", highs: "highs", awareness: "low awareness", tolerance: "tolerance", level2: "level 2 lows",
  rearm: "re-armed alarms", ketone_risk: "ketone risk", baseline_thin: "thin baseline",
  dose_mismatch: "dose mismatch", missed_injection: "missed injection", rise_high: "overnight rise high",
  rise_low: "overnight rise low",
};
const REASON = {
  clean: "clean", late_meal: "late meal", late_correction: "late correction", basal_late: "basal late",
  basal_missed: "basal missed", exercise: "exercise", treated_low: "treated low", stale: "sensor gap",
  away: "away",
};
const ACTION = {
  adjust_basal: "Adjust basal", schedule_visit: "Schedule visit", ask_patient: "Ask patient", dismiss: "Dismiss",
  proceed: "Proceed", hold_step: "Hold step", adjust_insulin: "Adjust insulin", message: "Message patient",
  end_watch: "End watch",
};
const RESOURCE = { gi_side_effect_education: "GI side-effect education" };
const GI = { fine: "fine", rough: "rough", cant_eat: "can't eat", missing: "no answer" };

// Keys that are shown inside another row (or their own section), not as rows.
const COMPANIONS = new Set(["nights", "window_low_point", "baseline_low_point", "insufficient", "excluded_nights"]);

const words = (key) => key.replace(/_/g, " ");
const n = (v) => (typeof v === "number" && Number.isFinite(v) ? v : null);
const num = (v) => (Number.isInteger(v) ? String(v) : String(Number(v.toFixed(1))));
const pct1 = (v) => `${v.toFixed(1)}%`;
const signed = (v) => (v > 0 ? `+${num(v)}` : v < 0 ? `−${num(-v)}` : "0");

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function day(iso) {
  // dates on a card are plain calendar dates: read them as text, never through a timezone
  const [y, m, d] = String(iso).slice(0, 10).split("-").map(Number);
  return { y, label: `${MONTHS[m - 1]} ${d}` };
}
function period(a, b) {
  const s = day(a), e = day(b);
  return s.y === e.y ? `${s.label} – ${e.label}, ${e.y}` : `${s.label}, ${s.y} – ${e.label}, ${e.y}`;
}

// How each labeled metric reads. `m` is the whole metrics object, for companions.
const METRIC = {
  clean_nights: ["Clean nights", (v, m) => (n(m.nights) !== null ? `${num(v)} of ${num(m.nights)}` : num(v))],
  rise_median_clean: ["Median overnight rise (clean nights)", (v) => `${signed(v)} mg/dL`],
  same_direction_share: ["Rose in the same direction", (v) => `${Math.round(v * 100)}%`],
  near_misses: ["Near-misses", num],
  escalated_warnings: ["Escalated warnings", num],
  rearms: ["Re-armed alarms", num],
  median_ack_min: ["Median time to acknowledge", (v) => `${num(v)} min`],
  nocturnal_lows: ["Night lows", num],
  answered: ["Morning questions answered", num],
  unfelt_lows: ["Lows not felt or not remembered", num],
  no_answer: ["Lows with no answer", num],
  unfelt_low_rate: ["Unfelt share of answered lows", (v) => `${Math.round(v * 100)}%`],
  inferred_unfelt_unanswered: ["Unanswered lows inferred unfelt", num],
  low_point_shift: ["Overnight low point vs baseline", (v, m) =>
    n(m.window_low_point) !== null && n(m.baseline_low_point) !== null
      ? `${signed(v)} mg/dL (${num(m.window_low_point)} vs ${num(m.baseline_low_point)})`
      : `${signed(v)} mg/dL`],
  baseline_nights: ["Baseline nights", num],
  coverage_pct: ["Sensor coverage", pct1],
  tbr_pct: ["Time below range", pct1],
  ketone_risk_episodes: ["Ketone-risk episodes", num],
};

function generic(v) {
  if (v === null || v === undefined) return null;
  if (typeof v === "number") return num(v);
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "string") return v;
  if (Array.isArray(v)) return `${v.length} item${v.length === 1 ? "" : "s"}`;
  return Object.entries(v).map(([k, x]) => `${words(k)} ${generic(x) ?? "—"}`).join(" · ");
}

function valueOf(key, m) {
  const v = m[key];
  if (v === null || v === undefined) return null;
  if (key === "tolerance" && typeof v === "object")
    return ["fine", "rough", "cant_eat", "missing"].filter((k) => k in v).map((k) => `${GI[k]} ${num(v[k])}`).join(" · ");
  if (key === "adherence" && typeof v === "object") {
    const parts = [];
    if (n(v.logged) !== null && n(v.expected) !== null) parts.push(`${num(v.logged)} of ${num(v.expected)} doses logged`);
    if (n(v.missed) !== null) parts.push(`${num(v.missed)} missed`);
    if (v.dose_mismatch === true) parts.push("dose mismatch");
    return parts.join(" · ") || generic(v);
  }
  const f = METRIC[key];
  if (f && typeof v === "number") return f[1](v, m);
  return generic(v);
}

/** @param {object} card a SignalCard; @param {{actions?: boolean, sample?: boolean}} opts */
export function cardModel(card, opts = {}) {
  const m = card.metrics || {};
  const conf = card.confidence || {};
  const status = STATUS[card.status] ? card.status : "insufficient";

  const badges = [{ text: STATUS[status], cls: "sc-status" }];
  badges.push({ text: PROGRAM[card.program] || words(String(card.program)), cls: "" });
  badges.push({ text: SOURCE[card.source] || words(String(card.source)), cls: "" });
  if (card.is_demo) badges.push({ text: "DEMO", cls: "sc-demo" });
  if (opts.sample) badges.push({ text: "Sample card", cls: "" });

  const kind = KIND[card.kind] || words(String(card.kind));
  const step = card.program === "step_watch" && Number.isInteger(card.step_index) ? ` · step ${card.step_index + 1}` : "";
  const meta = `${card.patient_pseudonym} · ${kind}${step} · ${period(card.period_start, card.period_end)}`;

  const rows = [];
  for (const key of Object.keys(conf)) {
    const label = METRIC[key] ? METRIC[key][0] : key === "tolerance" ? "Stomach (days)" : key === "adherence" ? "Weekly dose" : words(key);
    rows.push({ key, label, value: valueOf(key, m), conf: conf[key] });
  }
  for (const key of Object.keys(m)) {
    if (key in conf || COMPANIONS.has(key)) continue;
    rows.push({ key, label: words(key), value: valueOf(key, m), conf: null }); // shown, marked "no label"
  }

  const nights = (card.nights || []).map((nt) => {
    const codes = Array.isArray(nt.reason_codes) && nt.reason_codes.length ? nt.reason_codes : ["other"];
    const first = REASON[codes[0]] ? codes[0] : "other";
    const src = nt.code_source === "logged" ? "logged" : nt.code_source === "inferred" ? "inferred" : "unknown";
    const extra = [];
    if (n(nt.rise_mgdl) !== null) extra.push(`rise ${signed(nt.rise_mgdl)}`);
    if (n(nt.low_point_mgdl) !== null) extra.push(`low ${num(nt.low_point_mgdl)}`);
    if (n(nt.coverage_pct) !== null) extra.push(`${pct1(nt.coverage_pct)} covered`);
    return {
      date: day(nt.night_date).label,
      text: codes.map((c) => REASON[c] || words(String(c))).join(", "),
      cls: `sc-rc-${first}`,
      source: src,
      title: `${nt.night_date}: ${codes.map((c) => REASON[c] || c).join(", ")} (${src})${extra.length ? "; " + extra.join(", ") : ""}`,
    };
  });

  const excluded = Array.isArray(m.excluded_nights)
    ? m.excluded_nights.map((x) => ({
        date: day(x.night_date).label,
        reasons: (x.reasons || []).map((r) => REASON[r] || words(String(r))).join(", ") || "no reason given",
      }))
    : [];
  const excludedCounts = Object.entries(card.excluded_counts || {})
    .map(([k, c]) => `${num(c)} ${REASON[k] || words(k)}`)
    .join(" · ");

  const tolerance = (card.tolerance_days || []).map((d) => {
    const gi = GI[d.gi] ? d.gi : "missing";
    return { date: day(d.date).label, text: GI[gi], cls: `sc-gi-${gi}` };
  });

  const banner =
    m.insufficient === true || status === "insufficient"
      ? "Not enough data for a conclusion: too little of this window was covered by the sensor."
      : null;

  return {
    statusCls: `sc-${status}`,
    badges,
    meta,
    headline: card.headline,
    flags: (card.flags || []).map((f) => FLAG[f] || words(String(f))),
    banner,
    rows,
    nights,
    excluded,
    excludedCounts,
    tolerance,
    resources: (card.resource_categories || []).map((r) => RESOURCE[r] || words(String(r))),
    narrative: card.narrative,
    actions: opts.actions ? (card.allowed_actions || []).map((a) => ACTION[a] || words(String(a))) : [],
    foot: `Generated ${String(card.generated_at).replace("T", " ").slice(0, 16)} · ${card.card_id}`,
  };
}

export const CONF_TEXT = { measured: "measured", reported: "reported", inferred: "inferred" };
