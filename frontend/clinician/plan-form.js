// justin.md R5: the Step Watch plan setup form. Pre-filled from
// label-schedules.json (drug class, label and dose steps) and editable in every
// field; Accept builds a contracts.TitrationPlan and hands it to the inbox as a
// plan_create DoctorMessage (sealed, and applied only after the patient confirms
// on their Irin: invariant 8). The Pi refuses a plan that breaks the rules
// checked here (step_watch.validate_plan), so the form checks them first:
// steps indexed 0..n-1, planned starts strictly increasing, started_at =
// steps[0].planned_start.
const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
};
const CLASSES = { glp1: "GLP-1", gip_glp1: "GIP/GLP-1", weekly_basal: "Weekly basal insulin" };

// Calendar dates as YYYY-MM-DD text, shifted in UTC so no timezone moves a day.
const addDays = (iso, n) => new Date(Date.parse(`${iso}T00:00:00Z`) + n * 86400000).toISOString().slice(0, 10);
const daysBetween = (a, b) => Math.round((Date.parse(`${b}T00:00:00Z`) - Date.parse(`${a}T00:00:00Z`)) / 86400000);
const today = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};
const hex = (n) => [...crypto.getRandomValues(new Uint8Array(n))].map((b) => b.toString(16).padStart(2, "0")).join("");

let schedules = null;
async function loadSchedules() {
  if (schedules) return schedules;
  const res = await fetch("label-schedules.json", { cache: "no-store" });
  if (!res.ok) throw new Error(`label-schedules.json ${res.status}`);
  schedules = (await res.json()).schedules;
  return schedules;
}

/** The plan's rules, as the Pi checks them. Returns an error sentence or null. */
export function planError(plan) {
  if (!plan.drug_label.trim()) return "Name the drug.";
  if (!plan.steps.length) return "A plan needs at least one step.";
  for (const [i, st] of plan.steps.entries()) {
    if (st.index !== i) return "Steps must be numbered in order.";
    if (!st.dose_label.trim()) return `Type the dose for step ${i + 1}.`;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(st.planned_start)) return `Give step ${i + 1} a start date.`;
    if (i && st.planned_start <= plan.steps[i - 1].planned_start) return `Step ${i + 1} must start after step ${i}.`;
  }
  if (plan.started_at !== plan.steps[0].planned_start) return "The plan starts on its first step.";
  const off = plan.options.vigilance_offset_mgdl;
  if (!Number.isFinite(off) || off < 0 || off > 30) return "The vigilance offset is 0 to 30 mg/dL.";
  return null;
}

// ------------------------------------------------------------ the form

let ctx = null; // {isDemo, send}
let interval = 28;

function stepRow(dose, start) {
  const tr = el("tr");
  const dl = Object.assign(el("input"), { name: "dose", value: dose ?? "", maxLength: 40, placeholder: "type the dose" });
  const ds = Object.assign(el("input"), { type: "date", name: "start", value: start });
  const rm = Object.assign(el("button", "linkish", "remove"), { type: "button" });
  rm.addEventListener("click", () => { tr.remove(); renumber(); });
  tr.append(el("td", "stepno"), el("td"), el("td"), el("td"));
  tr.children[1].append(dl);
  tr.children[2].append(ds);
  tr.children[3].append(rm);
  return tr;
}
function renumber() {
  const rows = [...$("plansteps").children];
  rows.forEach((tr, i) => { tr.querySelector(".stepno").textContent = String(i + 1); });
  const first = rows[0] && rows[0].querySelector("[name=start]").value;
  if (first) $("planstart").value = first;
  $("planstartsat").textContent = first ? `The plan starts on ${first}, the first step's date.` : "";
}

function fillFrom(sch, start) {
  $("planclass").value = sch.drug_class;
  $("planlabel").value = sch.drug_label;
  $("planketone").checked = !!sch.ketone_prompts;
  interval = sch.interval_days;
  $("plansteps").replaceChildren(...sch.doses.map((d, i) => stepRow(d, addDays(start, i * sch.interval_days))));
  renumber();
}

/** Moving the start date moves every step by the same number of days, keeping the doses. */
function shiftStart() {
  const rows = [...$("plansteps").children];
  const first = rows[0] && rows[0].querySelector("[name=start]");
  const to = $("planstart").value;
  if (!first || !first.value || !to) return;
  const delta = daysBetween(first.value, to);
  for (const tr of rows) {
    const i = tr.querySelector("[name=start]");
    if (i.value) i.value = addDays(i.value, delta);
  }
  renumber();
}

function readPlan() {
  const steps = [...$("plansteps").children].map((tr, i) => ({
    index: i,
    dose_label: tr.querySelector("[name=dose]").value.trim(),
    planned_start: tr.querySelector("[name=start]").value,
  }));
  return {
    plan_id: `plan-${hex(8)}`,
    drug_class: $("planclass").value,
    drug_label: $("planlabel").value.trim(),
    steps,
    started_at: steps.length ? steps[0].planned_start : "",
    on_insulin: $("planinsulin").checked,
    options: {
      ketone_prompts: $("planketone").checked,
      step_week_vigilance: $("planvigil").checked,
      vigilance_offset_mgdl: Number($("planoffset").value),
    },
    status: "pending_confirm",
    is_demo: !!ctx.isDemo,
  };
}

async function accept(ev) {
  ev.preventDefault();
  const plan = readPlan();
  const err = planError(plan);
  if (err) return ($("planmsg").textContent = err);
  const msg = { message_id: `m-${hex(12)}`, kind: "plan_create", plan_id: plan.plan_id, plan, created_at: new Date().toISOString(), status: "pending" };
  const last = plan.steps[plan.steps.length - 1];
  const summary = `Start a Step Watch: ${plan.drug_label}, ${plan.steps.length} step${plan.steps.length === 1 ? "" : "s"}, ${plan.started_at} to ${last.planned_start}`;
  $("plansend").disabled = true;
  $("planmsg").textContent = "Sending…";
  const refused = await ctx.send(msg, summary);
  $("plansend").disabled = false;
  if (refused) return ($("planmsg").textContent = refused);
  $("plan").close();
}

let wired = false;
/** @param {{isDemo: boolean, send: (msg: object, summary: string) => Promise<string|null>}} opts
 * send returns null when the relay stored the message, or the sentence to show. */
export async function openPlanForm(opts) {
  ctx = opts;
  $("planmsg").textContent = "";
  $("plansend").disabled = false;
  let list;
  try {
    list = await loadSchedules();
  } catch (e) {
    $("planmsg").textContent = `Could not load the label schedules (${e.message}).`;
    list = [];
  }
  if (!wired) {
    wired = true;
    $("planclass").replaceChildren(...Object.entries(CLASSES).map(([v, t]) => Object.assign(el("option", "", t), { value: v })));
    $("planschedule").replaceChildren(...list.map((sch) => Object.assign(el("option", "", `${sch.drug_label} · every ${sch.interval_days} days`), { value: sch.id })));
    $("planschedule").addEventListener("change", () => {
      const sch = list.find((x) => x.id === $("planschedule").value);
      if (sch) fillFrom(sch, $("planstart").value || today());
    });
    $("planstart").addEventListener("change", shiftStart);
    $("plansteps").addEventListener("change", renumber);
    $("planadd").addEventListener("click", () => {
      const rows = [...$("plansteps").children];
      const lastStart = rows.length ? rows[rows.length - 1].querySelector("[name=start]").value : "";
      $("plansteps").append(stepRow(null, lastStart ? addDays(lastStart, interval) : $("planstart").value || today()));
      renumber();
    });
    $("planvigil").addEventListener("change", () => { $("planoffset").disabled = !$("planvigil").checked; });
    $("planform").addEventListener("submit", accept);
    $("plancancel").addEventListener("click", () => $("plan").close());
  }
  $("planstart").value = today();
  $("planinsulin").checked = true;
  $("planvigil").checked = false;
  $("planoffset").value = "10";
  $("planoffset").disabled = true;
  if (list.length) {
    $("planschedule").value = list[0].id;
    fillFrom(list[0], today());
  }
  $("plan").showModal();
}
