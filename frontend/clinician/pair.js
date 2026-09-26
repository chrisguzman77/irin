import { b64, code4, load, nacl, save, unb64 } from "./session.js";

// justin.md R5 pairing. The patient's Irin shows a QR for
//   <inbox>/pair#token=<32 hex>&device_pk=<base64>&relay=<url>
// (everything after # stays off every server). This page: reads and strips the
// fragment, asks the relay about the token, generates the doctor keypair (the
// private key stays in this browser), POSTs {relay}/v0/pair/{token}/complete
// {doctor_pk, doctor_display_name}, shows code4, and polls the token until the
// patient confirms on their Irin; the relay then hands the bearer over ONCE.
// No typed-code path at the hackathon.
const $ = (id) => document.getElementById(id);
const POLL_MS = 2000;
const show = (id) => { for (const s of ["ask", "code", "done"]) $(s).hidden = s !== id; };
const say = (t) => { $("msg").textContent = t; };

/** Split by hand: base64 carries "+", which URLSearchParams would turn into a space. */
function readFragment(hash) {
  const out = {};
  for (const part of hash.replace(/^#/, "").split("&")) {
    const i = part.indexOf("=");
    if (i > 0) out[part.slice(0, i)] = decodeURIComponent(part.slice(i + 1));
  }
  return out;
}
function validFragment(f) {
  if (!/^[0-9a-f]{32}$/.test(f.token || "")) return false;
  try {
    if (unb64(f.device_pk).length !== 32) return false;
    const u = new URL(f.relay);
    return u.protocol === "https:" || u.protocol === "http:";
  } catch {
    return false;
  }
}

async function tokenState(s) {
  const res = await fetch(`${s.relay.replace(/\/$/, "")}/v0/pair/${s.token}`, { cache: "no-store" });
  if (res.status === 404) return { status: "expired" };
  if (!res.ok) throw new Error(`relay answered ${res.status}`);
  return res.json();
}

let pollTimer = null;
async function poll(s) {
  let st;
  try {
    st = await tokenState(s);
  } catch {
    return say("Cannot reach the relay. Still trying…");
  }
  say("");
  if (st.status === "expired") {
    clearInterval(pollTimer);
    show(null);
    return say("This pairing code expired. Ask the patient to show a new one.");
  }
  if (st.status === "revoked") {
    clearInterval(pollTimer);
    show(null);
    return say("The patient cancelled this pairing.");
  }
  if (st.status !== "confirmed") return;
  clearInterval(pollTimer);
  if (!st.bearer) {
    show(null);
    return say("The patient confirmed, but this browser did not receive the key (it is handed out once). Ask the patient to pair again.");
  }
  save({ ...s, state: "paired", bearer: st.bearer, doctor_id: st.doctor_id, device_id: st.device_id || s.device_id,
    paired_at: new Date().toISOString() });
  show("done");
}

function waitForConfirm(s) {
  $("code4").textContent = code4(s.device_pk, s.doctor_pk, s.token);
  show("code");
  clearInterval(pollTimer);
  pollTimer = setInterval(() => poll(s), POLL_MS);
  poll(s);
}

async function main() {
  const f = readFragment(location.hash);
  if (location.hash) history.replaceState(null, "", location.pathname); // the token does not linger in the address bar
  const stored = load();

  // a reload while waiting: carry on with the key already made for this token
  if (!f.token && stored && stored.state === "joining") {
    $("demo").hidden = !stored.is_demo;
    return waitForConfirm(stored);
  }
  if (!f.token && stored && stored.state === "paired") return show("done");
  if (!validFragment(f)) return say("This link is not a complete pairing code. Scan the QR on the patient's Irin again.");

  let st;
  try {
    st = await tokenState(f);
  } catch {
    return say("Cannot reach the relay. Check the connection and scan again.");
  }
  if (st.status === "expired") return say("This pairing code expired. Ask the patient to show a new one.");
  if (st.status !== "pending") return say("This pairing code was already used. Ask the patient to show a new one.");
  if (st.device_pk !== f.device_pk) return say("The relay's device key does not match the QR code. Not pairing.");
  $("demo").hidden = !st.is_demo;

  if (stored && stored.state === "paired") {
    say(`This browser is already paired with a patient's Irin. Joining replaces that pairing.`);
  }
  show("ask");
  $("join").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const name = $("name").value.trim();
    if (!name) return;
    const btn = $("join").querySelector("button");
    btn.disabled = true;
    const kp = nacl().box.keyPair();
    const s = { relay: f.relay, token: f.token, device_pk: f.device_pk, device_id: st.device_id || "", is_demo: !!st.is_demo,
      name, doctor_pk: b64(kp.publicKey), doctor_sk: b64(kp.secretKey), state: "joining" };
    try {
      save(s); // before the relay hears the key: a reload must not lose it
    } catch {
      btn.disabled = false;
      return say("This browser does not allow saving the key (private mode?). Pairing needs it.");
    }
    try {
      const res = await fetch(`${f.relay.replace(/\/$/, "")}/v0/pair/${f.token}/complete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ doctor_pk: s.doctor_pk, doctor_display_name: name }),
      });
      if (!res.ok) {
        btn.disabled = false;
        return say(res.status === 409 ? "This pairing code was already used. Ask the patient to show a new one." : `The relay refused (${res.status}).`);
      }
    } catch {
      btn.disabled = false;
      return say("Cannot reach the relay. Try Join again.");
    }
    waitForConfirm(s);
  });
}

main();
