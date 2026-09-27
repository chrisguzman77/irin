import { useEffect, useState } from "react";
import { PinRejected } from "../../lib/api";
import { claimHub, getHub, getProfile, saveBuddySettings, saveProfile, type HubClaim, type HubRow } from "../../lib/buddy";
import type { Settings } from "../../lib/contracts";

// Buddy v3 (relay/README.md, PINNED): the Irin Buddy Hub for app users. The
// Buddy home offers "Join the Irin Buddy Hub" (turns on hub_volunteer, then
// re-saves the directory profile's opt-ins) or "Go to Hub". The hub lists
// people in the user's pool who share a language; a row carries a first name,
// languages, minutes, and a confidence badge, never a glucose value, place, or
// contact (invariant 15). The script is shown only from the live claim's reply
// (invariant 14); the Call button places no call in the demo.
const OFF = { have_buddy: false, be_watcher: false, hub_watchable: false, hub_volunteer: false };
const REFRESH_MS = 15_000;
const demoBadge = <span className="bg-amber-400 text-black text-xs font-bold px-2 py-0.5 rounded">DEMO</span>;
const sampleBadge = <span className="border border-neutral-500 text-neutral-300 text-xs px-2 py-0.5 rounded">Sample</span>;

/** device-confirmed and unconfirmed must look different at a glance (invariant 15) */
function ConfidenceBadge({ c }: { c: HubRow["confidence"] }) {
  return c === "device_confirmed" ? (
    <span className="bg-emerald-500 text-black text-xs font-bold px-2 py-0.5 rounded">✓ Device-confirmed</span>
  ) : (
    <span className="border border-dashed border-amber-400 text-amber-300 text-xs px-2 py-0.5 rounded">? Unconfirmed</span>
  );
}

export function HubEntry({ base, settings, onOpen }: { base: string; settings: Settings | undefined; onOpen: () => void }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  if (settings?.night_buddy?.hub_volunteer)
    return (
      <button type="button" className="rounded-lg px-3 py-3 bg-sky-400 text-black font-semibold" onClick={onOpen}>
        Go to Hub
      </button>
    );
  const join = async () => {
    setBusy(true);
    setMsg("");
    try {
      const optins = { ...OFF, ...(settings?.night_buddy ?? {}), hub_volunteer: true };
      const s = await saveBuddySettings(base, { night_buddy: optins });
      if (!s.ok) return setMsg(s.reason);
      const p = await getProfile(base);
      if (!p.ok) return setMsg(p.reason);
      if (p.value.profile) {
        const r = await saveProfile(base, { ...p.value.profile, optins });
        if (!r.ok) return setMsg(r.reason);
      }
      setMsg("Joined. Waiting for your Irin to confirm…");
    } catch (e) {
      if (!(e instanceof PinRejected)) setMsg("Could not reach your Irin.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="flex flex-col gap-2">
      <button type="button" disabled={busy} className="rounded-lg px-3 py-3 bg-sky-400 text-black font-semibold disabled:opacity-40" onClick={join}>
        {busy ? "Joining…" : "Join the Irin Buddy Hub"}
      </button>
      <p className="text-xs text-neutral-500">You may see people you have never met and help one by following their own script.</p>
      {msg && <p role="status" className="text-sm text-amber-300">{msg}</p>}
    </div>
  );
}

export default function HubScreen({ base, demo, onBack }: { base: string; demo: boolean; onBack: () => void }) {
  const [rows, setRows] = useState<HubRow[] | null>(null);
  const [open, setOpen] = useState<HubRow | null>(null);
  const [claim, setClaim] = useState<HubClaim | null>(null);
  const [called, setCalled] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  // the list refreshes every 15 s while it is on screen
  useEffect(() => {
    if (open) return;
    let live = true;
    const load = () =>
      getHub(base)
        .then((r) => {
          if (!live) return;
          if (r.ok) {
            setRows(r.value);
            setMsg("");
          } else setMsg(r.reason);
        })
        .catch((e) => live && !(e instanceof PinRejected) && setMsg("Could not reach your Irin."));
    void load();
    const id = window.setInterval(load, REFRESH_MS);
    return () => {
      live = false;
      window.clearInterval(id);
    };
  }, [base, open]);

  const help = async (row: HubRow) => {
    setBusy(true);
    setMsg("");
    try {
      const r = await claimHub(base, row.listing_id);
      if (r.ok) setClaim(r.value);
      else setMsg(r.reason);
    } catch (e) {
      if (!(e instanceof PinRejected)) setMsg("Could not reach your Irin.");
    } finally {
      setBusy(false);
    }
  };
  const close = () => {
    setOpen(null);
    setClaim(null);
    setCalled(false);
    setMsg("");
  };

  const back = (
    <button type="button" className="self-start rounded-lg px-4 py-2 bg-neutral-800 text-neutral-200" onClick={open ? close : onBack}>
      ← Back
    </button>
  );

  if (open)
    return (
      <section className="flex flex-col gap-3">
        {back}
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-2xl font-semibold">{open.first_name}</h3>
          <ConfidenceBadge c={open.confidence} />
          {open.sample && sampleBadge}
          {demo && demoBadge}
        </div>
        {open.languages.length > 0 && <p className="text-sm text-neutral-300">Speaks {open.languages.join(", ")}</p>}
        {!claim ? (
          <button type="button" disabled={busy} className="rounded-lg px-3 py-3 bg-sky-400 text-black font-semibold disabled:opacity-40"
            onClick={() => help(open)}>
            {busy ? "Claiming…" : `Help ${open.first_name}`}
          </button>
        ) : (
          <>
            <h4 className="text-xs uppercase tracking-wider text-neutral-400">{open.first_name}'s script: follow it exactly</h4>
            <ol className="flex flex-col gap-2">
              {claim.steps.map((s, i) => (
                <li key={i} className="flex gap-2">
                  <span className="text-neutral-500 tabular-nums w-5 text-right">{i + 1}.</span>
                  <span className="flex-1 text-neutral-100">{s}</span>
                </li>
              ))}
            </ol>
            {claim.steps.length === 0 && <p className="text-sm text-neutral-400">No script was shared.</p>}
            <button type="button" className="rounded-2xl py-6 bg-emerald-500 text-black text-2xl font-bold" onClick={() => setCalled(true)}>
              Call
            </button>
            {called && <p role="status" className="text-sm text-amber-300">Calling is simulated in the demo.</p>}
          </>
        )}
        {msg && <p role="status" className="text-sm text-red-400">{msg}</p>}
      </section>
    );

  return (
    <section className="flex flex-col gap-3">
      {back}
      <div className="flex items-center gap-2">
        <h3 className="text-lg font-semibold">Irin Buddy Hub</h3>
        {demo && demoBadge}
      </div>
      {rows === null && !msg && <p className="text-sm text-neutral-400 animate-pulse">Loading…</p>}
      {rows?.length === 0 && <p className="text-sm text-neutral-400">No one needs help right now</p>}
      <ul className="flex flex-col gap-2">
        {rows?.map((r) => (
          <li key={r.listing_id}>
            <button type="button" className="w-full text-left rounded-lg bg-neutral-900 p-3 flex flex-col gap-1" onClick={() => setOpen(r)}>
              <span className="flex flex-wrap items-center gap-2">
                <span className="font-semibold">{r.first_name}</span>
                <ConfidenceBadge c={r.confidence} />
                {r.sample && sampleBadge}
              </span>
              <span className="text-sm text-neutral-300">Low for {r.elapsed_min} min</span>
              {r.languages.length > 0 && <span className="text-xs text-neutral-500">{r.languages.join(", ")}</span>}
            </button>
          </li>
        ))}
      </ul>
      {msg && <p role="status" className="text-sm text-red-400">{msg}</p>}
    </section>
  );
}
