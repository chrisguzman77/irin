// The clinician inbox's card renderer (justin.md R5). Draws the model from
// card-model.js with the classes in card.css; the app's SignalCard component
// draws the same model with the same classes, so both look identical.
// textContent only: card text is data, never HTML.
import { cardModel, CONF_TEXT } from "./card-model.js";

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
}

function section(title) {
  const s = el("section", "sc-section");
  s.append(el("h4", "sc-h", title));
  return s;
}

// Actions whose flow belongs elsewhere: shown, never sendable from this button.
// "resources" is built (the inbox hands off from its own Resources side panel,
// not from ACTIONS in inbox.js), so it stays disabled but points there instead
// of claiming to be unbuilt.
const LATER = { resources: "use Resources in the side panel" };

/** @param {object} card a SignalCard
 * @param {{actions?: boolean, sample?: boolean, onAction?: (key: string) => void, onResource?: (category: string) => void, onNight?: (index: number) => void}} opts
 * @returns {HTMLElement} the card, ready to insert */
export function renderCard(card, opts = {}) {
  const m = cardModel(card, opts);
  const root = el("article", `sc-card ${m.statusCls}`);

  const badges = el("div", "sc-badges");
  for (const b of m.badges) badges.append(el("span", `sc-badge ${b.cls}`.trim(), b.text));
  root.append(badges, el("div", "sc-meta", m.meta), el("p", "sc-headline", m.headline));

  if (m.flags.length) {
    const f = el("div", "sc-flags");
    for (const x of m.flags) f.append(el("span", "sc-flag", x));
    root.append(f);
  }
  if (m.banner) root.append(el("div", "sc-banner", m.banner));

  if (m.rows.length) {
    const s = section("Numbers");
    const t = el("table", "sc-metrics");
    const body = el("tbody");
    for (const r of m.rows) {
      const tr = el("tr");
      tr.append(el("td", "", r.label));
      tr.append(r.value === null ? el("td", "sc-v sc-nodata", "no data") : el("td", "sc-v", r.value));
      const c = el("td", "sc-c");
      c.append(r.conf ? el("span", `sc-conf sc-${r.conf}`, CONF_TEXT[r.conf] || r.conf) : el("span", "sc-conf sc-nolabel", "no label"));
      tr.append(c);
      body.append(tr);
    }
    t.append(body);
    s.append(t);
    root.append(s);
  }

  if (m.nights.length) {
    const s = section("Nights");
    const strip = el("div", "sc-strip");
    const tap = typeof opts.onNight === "function"; // the inbox's night replay
    m.nights.forEach((nt, i) => {
      const d = el(tap ? "button" : "div", `sc-night ${nt.cls} ${nt.source === "logged" ? "sc-logged" : "sc-inferred-code"}`);
      d.title = nt.title;
      d.append(el("b", "", nt.date), nt.text);
      if (tap) {
        d.type = "button";
        d.addEventListener("click", () => opts.onNight(i));
      }
      strip.append(d);
    });
    s.append(strip, el("div", "sc-legend", "Solid border: reason logged. Dashed: inferred from the trace."));
    root.append(s);
  }

  if (m.excluded.length || m.excludedCounts) {
    const s = section("Left out of the comparison");
    if (m.excludedCounts) s.append(el("div", "sc-meta", m.excludedCounts));
    if (m.excluded.length) {
      const ul = el("ul", "sc-list");
      for (const x of m.excluded) ul.append(el("li", "", `${x.date}: ${x.reasons}`));
      s.append(ul);
    }
    root.append(s);
  }

  if (m.tolerance.length) {
    const s = section("Stomach, day by day");
    const g = el("div", "sc-gi");
    for (const d of m.tolerance) g.append(el("span", d.cls, `${d.date} ${d.text}`));
    s.append(g);
    root.append(s);
  }

  if (m.resources.length) {
    // the inbox passes onResource(category) to start the handoff flow, a preview lists them
    const s = section("Resources");
    if (typeof opts.onResource === "function") {
      const a = el("div", "sc-actions");
      (card.resource_categories || []).forEach((key, i) => {
        const b = el("button", "sc-action", m.resources[i]);
        b.type = "button";
        b.addEventListener("click", () => opts.onResource(String(key)));
        a.append(b);
      });
      s.append(a);
    } else s.append(el("div", "", m.resources.join(" · ")));
    root.append(s);
  }

  const n = section("Summary");
  n.append(el("p", "sc-narrative", m.narrative));
  root.append(n);

  if (m.actions.length) {
    // m.actions are the labels of card.allowed_actions, in the same order; the
    // inbox passes onAction(key) to make them live, a preview leaves them off.
    const live = typeof opts.onAction === "function";
    const s = section(live ? "Actions" : "Actions (preview)");
    const a = el("div", "sc-actions");
    const keys = card.allowed_actions || [];
    m.actions.forEach((label, i) => {
      const key = String(keys[i]);
      const later = LATER[key];
      const b = el("button", "sc-action", later ? `${label} (${later})` : label);
      b.type = "button";
      b.disabled = !live || !!later;
      if (live && !later) b.addEventListener("click", () => opts.onAction(key));
      a.append(b);
    });
    s.append(a);
    root.append(s);
  }

  root.append(el("div", "sc-foot", m.foot));
  return root;
}
