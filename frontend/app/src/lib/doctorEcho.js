// Doctor-message echo (justin.md R4, invariant 8): the plain words the confirm
// takeover shows for a DoctorMessage (contracts.py). ONE file for both
// screens: the kiosk loads it as a <script>, the app imports a byte-identical
// copy (frontend/app/src/lib/doctorEcho.js); keep the two equal. Pure; it
// defines globalThis.irinDoctorEcho(message, doctorName, plan) and nothing else.
(function (g) {
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function day(iso) {
    // a plain calendar date: read as text, never through a timezone
    var p = String(iso).slice(0, 10).split("-").map(Number);
    return MONTHS[p[1] - 1] + " " + p[2];
  }
  function units(v) {
    return (Number.isInteger(v) ? String(v) : String(Number(v.toFixed(1)))) + (v === 1 ? " unit" : " units");
  }

  /**
   * @param {object} m a DoctorMessage
   * @param {string|null} doctorName the paired doctor's display name
   * @returns {{who: string, line: string, note: string|null, insulin: string|null, confirm: string, known: boolean}}
   *   insulin: the units exactly as they will be applied (shown large; the
   *   same echo rule as voice logging), or null when no insulin changes.
   */
  function echo(m, doctorName) {
    var who = doctorName && String(doctorName).trim() ? String(doctorName).trim() : "Your doctor";
    var note = m.text && String(m.text).trim() ? String(m.text).trim() : null;
    var from = m.start_date ? " from " + day(m.start_date) : "";
    var line, insulin = null, confirm = "Confirm", known = true;
    switch (m.kind) {
      case "insulin_change":
        if (typeof m.new_units === "number" && isFinite(m.new_units)) {
          insulin = units(m.new_units);
          line = (m.insulin === "bolus" ? "bolus" : "basal") + " " + insulin + from + ".";
        } else {
          line = "an insulin change with no amount."; // never guess a number
          known = false;
        }
        break;
      case "hold_step":
        line = m.hold_weeks ? "hold the next step " + m.hold_weeks + " weeks." : "hold the next step.";
        break;
      case "proceed":
        line = "go ahead with the next step as planned.";
        break;
      case "plan_create":
      case "plan_update": {
        var p = m.plan;
        var verb = m.kind === "plan_create" ? "start a Step Watch" : "update the Step Watch plan";
        if (p && p.steps && p.steps.length) {
          var first = p.steps[0], next = p.steps[1];
          line = verb + ": " + p.drug_label + " " + first.dose_label +
            (next ? ", first increase on " + day(next.planned_start) : "") + ".";
        } else {
          line = verb + ".";
        }
        break;
      }
      case "end_watch":
        line = "end the Step Watch.";
        break;
      case "schedule_request":
        line = "please schedule a visit.";
        confirm = "OK";
        break;
      case "note":
        line = note ? note : "a note with no text.";
        note = null;
        confirm = "Got it";
        break;
      case "dismiss":
        line = "no change needed.";
        confirm = "OK";
        break;
      default:
        line = "a message this screen cannot show.";
        known = false;
    }
    return { who: who, line: line, note: note, insulin: insulin, confirm: confirm, known: known };
  }

  g.irinDoctorEcho = echo;
})(typeof window !== "undefined" ? window : globalThis);
