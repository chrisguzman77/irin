import { useState } from "react";
import { PinRejected } from "../../lib/api";
import { hhmm, postTreating, readBuddyState } from "../../lib/buddy";
import type { StateSnapshot } from "../../lib/contracts";

// B1: one giant "I'm treating" while the snapshot's buddy_state.open_alert is
// set. ONE thumb press, no confirmation step (invariant 17): the tap posts at
// once, and the Pi's treating_set is what says it took. It never touches the
// local alarm (invariant 13): acknowledging stays the AckBar's job.
export default function TreatingButton({ snap, baseUrl }: { snap: StateSnapshot | null; baseUrl: string }) {
  const [sending, setSending] = useState(false);
  const [err, setErr] = useState("");
  const { openAlert, treating } = readBuddyState(snap);
  if (!openAlert) return null;

  const press = async () => {
    if (sending) return;
    setSending(true);
    setErr("");
    try {
      const r = await postTreating(baseUrl);
      if (!r.ok) setErr(r.reason);
    } catch (e) {
      if (!(e instanceof PinRejected)) setErr("Could not reach your Irin.");
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="mb-3 flex flex-col gap-2">
      <button
        type="button"
        onClick={press}
        disabled={sending}
        className="w-full min-h-40 rounded-2xl bg-sky-400 text-black text-4xl font-black tracking-tight active:bg-sky-300 disabled:opacity-70"
      >
        I&apos;m treating
      </button>
      {treating?.since ? (
        <p role="status" className="text-sm text-sky-200 text-center">
          Marked treating at {hhmm(treating.since)}
          {treating.expires_at ? `; your buddy stands by until ${hhmm(treating.expires_at)}` : ""}. Your alarm still sounds
          until you acknowledge it.
        </p>
      ) : (
        <p className="text-sm text-neutral-400 text-center">Your buddy has been alerted. One tap tells them you are on it.</p>
      )}
      {err && <p role="status" className="text-sm text-red-400 text-center">{err}</p>}
    </div>
  );
}
