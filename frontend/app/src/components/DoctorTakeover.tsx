import { useEffect, useRef, useState } from "react";
import { deviceFetch } from "../lib/api";
import { soundingLow } from "../lib/alarm";
import { useDevice } from "../lib/device";
import { echoDoctorMessage, messagePath, pairedDoctorName, type DoctorMessage } from "../lib/doctorMessages";
import { cancelFreshPrompt, postFresh } from "../lib/freshPin";

// R4: the doctor-message confirm takeover in the app, mirroring the kiosk.
// Echoes the oldest pending message in plain words (the same doctor-echo file
// as the kiosk); an insulin change shows its units large. Confirm and Decline
// are fresh-PIN verbs (always prompt). Never auto-confirms, never closes on
// its own: it disappears when the Pi's doctor_message_resolved arrives. A
// sounding low alarm outranks it, so it steps aside while one is on.
export default function DoctorTakeover() {
  const { target, socket } = useDevice();
  const [msg, setMsg] = useState<{ id: string; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const asking = useRef<string | null>(null);

  const snap = socket.snapshot;
  const pending = (snap?.pending_doctor_messages ?? []) as DoctorMessage[];
  const m = pending[0];
  const pendingIds = pending.map((x) => x.message_id).join(",");
  // Each message's own sender name (GET /api/rounds/messages carries
  // doctor_display_name per message, e.g. "Impiricus Spark (simulated)"); the
  // snapshot's DoctorMessage has none, so the paired-doctor rule is the fallback.
  const [senders, setSenders] = useState<Record<string, string>>({});
  const base = target.status === "ready" ? target.url : null;
  useEffect(() => {
    if (!base || !pendingIds) return;
    let alive = true;
    deviceFetch(base, "/api/rounds/messages", { pinned: true, cache: "no-store" })
      .then((r) => (r.ok ? r.json() : []))
      .then((docs: unknown) => {
        if (!alive || !Array.isArray(docs)) return;
        const out: Record<string, string> = {};
        for (const d of docs as { message?: { message_id?: unknown }; doctor_display_name?: unknown }[]) {
          const id = d?.message?.message_id;
          if (typeof id === "string" && typeof d.doctor_display_name === "string" && d.doctor_display_name.trim())
            out[id] = d.doctor_display_name.trim();
        }
        setSenders(out);
      })
      .catch(() => {
        /* the fallback name stays */
      });
    return () => {
      alive = false;
    };
  }, [base, pendingIds]);

  // settled elsewhere (the kiosk, expiry) while the PIN prompt was open: close
  // it, so a PIN is never typed for a message that is gone
  useEffect(() => {
    if (asking.current && !pendingIds.split(",").includes(asking.current)) cancelFreshPrompt();
  }, [pendingIds]);
  if (target.status !== "ready" || !snap || !m || soundingLow(snap.alarm)) return null;

  const e = echoDoctorMessage(m, senders[m.message_id] ?? pairedDoctorName(snap.pairing_state));
  const note = msg?.id === m.message_id ? msg.text : null;

  const answer = async (verb: "confirm" | "decline") => {
    if (busy) return;
    setBusy(true);
    asking.current = m.message_id;
    try {
      const res = await postFresh(
        target.url,
        messagePath(m.message_id, verb),
        undefined,
        verb === "confirm" ? "Enter your PIN to confirm" : "Enter your PIN to decline",
      );
      if (res.ok) setMsg({ id: m.message_id, text: "Sent. Waiting for your Irin…" });
      else if (!res.cancelled) setMsg({ id: m.message_id, text: res.reason });
    } catch {
      setMsg({ id: m.message_id, text: "Could not reach your Irin. Nothing was confirmed." });
    } finally {
      asking.current = null;
      setBusy(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Message from ${e.who}`}
      className="fixed inset-0 z-40 bg-[#071a2e] text-[#e8f1fb] flex flex-col items-center justify-center gap-4 p-6 text-center"
    >
      {snap.mode === "replay" && (
        // the overlay covers the status bar, so it carries its own DEMO badge (invariant 1)
        <div className="absolute top-0 inset-x-0 bg-amber-400 text-black text-center font-black tracking-widest py-2">
          DEMO — replayed data, not live
        </div>
      )}
      {pending.length > 1 && <div className="text-sm text-[#8fb3d9]">1 of {pending.length} messages</div>}
      <div className="text-lg font-bold text-[#8fb3d9]">{e.who}:</div>
      <div className="text-2xl font-bold leading-snug max-w-md">{e.line}</div>
      {e.insulin && <div className="text-6xl font-extrabold tabular-nums">{e.insulin}</div>}
      {e.note && <div className="text-base text-[#cfe0f2] max-w-md">{e.note}</div>}
      <div className="flex gap-3 w-full max-w-sm mt-2">
        <button
          type="button"
          disabled={busy}
          onClick={() => answer("decline")}
          className="flex-1 py-4 rounded-xl border-2 border-[#8fb3d9] text-lg font-bold disabled:opacity-40"
        >
          Decline
        </button>
        <button
          type="button"
          disabled={busy || !e.known}
          onClick={() => answer("confirm")}
          className="flex-1 py-4 rounded-xl bg-[#e8f1fb] text-[#071a2e] text-lg font-extrabold disabled:opacity-35"
        >
          {e.confirm}
        </button>
      </div>
      <div role="status" className="text-sm text-[#ffd27a] min-h-5">
        {note ?? (e.known ? "" : "This message cannot be confirmed here. Decline it, and ask for it again.")}
      </div>
    </div>
  );
}
