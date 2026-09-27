import { useEffect, useRef, useState } from "react";
import { PinRejected } from "../../lib/api";
import { useDevice } from "../../lib/device";
import { confirmPairing, pairStatus, qrPath, readPairingState, revokePairing, startPairing } from "../../lib/pairing";

// R3 pairing status + Revoke, and R1's "Share with my doctor" on the phone.
// The phone that starts a pairing shows the QR itself (only the starter holds
// the token) and polls the Pi until the doctor's browser joins; then both the
// phone and the bedside screen show the four-digit code and a Confirm that
// asks for the PIN every time. Nothing is shared until Confirm succeeds.
const POLL_MS = 3000;

function since(iso: string | null | undefined): string {
  if (!iso) return "";
  const M = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const [, m, d] = iso.slice(0, 10).split("-").map(Number);
  return m ? ` · since ${M[m - 1]} ${d}` : "";
}

function Qr({ text }: { text: string }) {
  const { size, d } = qrPath(text);
  return (
    <div className="bg-white p-3 rounded-lg self-center">
      <svg viewBox={`0 0 ${size} ${size}`} className="w-64 h-64 block" shapeRendering="crispEdges" role="img" aria-label="pairing QR code">
        <path d={d} fill="#000" />
      </svg>
    </div>
  );
}

export default function DoctorSharing() {
  const { target, socket } = useDevice();
  const base = target.status === "ready" ? target.url : null;
  const ps = readPairingState(socket.snapshot?.pairing_state);
  const demo = socket.snapshot?.mode === "replay";
  const [qr, setQr] = useState<{ url: string; demo: boolean; deadline: number } | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [askRevoke, setAskRevoke] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const seen = useRef(false); // the Pi has reported our pairing as pending at least once

  // while our QR is up: ask the Pi whether the doctor joined (it broadcasts
  // pairing_state on each change), and tick the countdown
  useEffect(() => {
    if (!qr || !base) return;
    const id = window.setInterval(() => {
      setNow(Date.now());
      pairStatus(base).catch(() => {});
    }, POLL_MS);
    pairStatus(base).catch(() => {});
    return () => window.clearInterval(id);
  }, [qr, base]);

  // our pairing ended on the Pi (confirmed on either screen, expired, mode change)
  useEffect(() => {
    if (!qr) return;
    if (ps.status !== "idle") seen.current = true;
    else if (seen.current) setQr(null);
  }, [qr, ps.status]);
  useEffect(() => {
    if (qr && now > qr.deadline) {
      setQr(null);
      setMsg("The code expired. Start again when your doctor is ready.");
    }
  }, [qr, now]);

  if (!base) return null;

  const run = async (f: () => Promise<void>) => {
    if (busy) return;
    setBusy(true);
    setMsg(null);
    try {
      await f();
    } catch (e) {
      if (!(e instanceof PinRejected)) setMsg("Could not reach your Irin. Nothing changed.");
    } finally {
      setBusy(false);
    }
  };
  const start = () =>
    run(async () => {
      const res = await startPairing(base);
      if (!res.ok) return setMsg(res.reason);
      seen.current = false;
      setCopied(false);
      setNow(Date.now());
      setQr({ url: res.value.qr_url, demo: res.value.is_demo, deadline: Date.now() + (res.value.expires_in_s || 600) * 1000 });
    });
  const confirm = () =>
    run(async () => {
      const res = await confirmPairing(base);
      if (res.ok) {
        const name = (res.value as { doctor_display_name?: unknown } | null)?.doctor_display_name;
        setMsg(`Now sharing with ${typeof name === "string" ? name : "your doctor"}.`);
      } else if (!res.cancelled) setMsg(res.reason);
    });
  const revoke = (id: string, name: string) =>
    run(async () => {
      const res = await revokePairing(base, id);
      setAskRevoke(null);
      setMsg(res.ok ? `Stopped sharing with ${name}.` : res.reason);
    });
  const copy = async (url: string) => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
    } catch {
      setMsg("Could not copy. Show the QR code instead.");
    }
  };

  const paired = ps.pairings.filter((p) => p.status === "paired" && (p.peer_kind ?? "doctor") === "doctor");
  const left = qr ? Math.max(0, Math.round((qr.deadline - now) / 1000)) : 0;
  const joined = ps.status === "awaiting_confirm" && ps.code4;

  return (
    <section className="flex flex-col gap-3 rounded-xl border border-neutral-800 bg-neutral-950 p-4" aria-label="my doctor">
      <h3 className="text-sm uppercase tracking-wider text-neutral-400">My doctor</h3>
      {paired.length === 0 && ps.status === "idle" && (
        <p className="text-neutral-300">Not sharing with anyone. Your doctor sees nothing until you pair.</p>
      )}
      {paired.map((p) => (
        <div key={p.doctor_id} className="flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <span className="flex-1">
              <span className="font-semibold">{p.doctor_display_name}</span>
              <span className="text-sm text-neutral-400">{since(p.confirmed_at)}</span>
            </span>
            {p.is_demo && <span className="bg-amber-400 text-black text-xs font-bold px-2 py-0.5 rounded">DEMO</span>}
            {askRevoke !== p.doctor_id && (
              <button type="button" onClick={() => setAskRevoke(p.doctor_id)} className="text-sm text-red-300 underline">
                Revoke
              </button>
            )}
          </div>
          {askRevoke === p.doctor_id && (
            <div className="rounded-lg bg-neutral-900 p-3 flex flex-col gap-2">
              <p className="text-sm">
                Stop sharing with {p.doctor_display_name}? Their inbox stops receiving cards now. To share again you pair again.
              </p>
              <div className="flex gap-2">
                <button type="button" onClick={() => setAskRevoke(null)} className="flex-1 rounded-lg bg-neutral-800 py-2">
                  Keep sharing
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => revoke(p.doctor_id, p.doctor_display_name)}
                  className="flex-1 rounded-lg bg-red-700 py-2 font-semibold disabled:opacity-40"
                >
                  Stop sharing
                </button>
              </div>
            </div>
          )}
        </div>
      ))}

      {joined ? (
        <div className="flex flex-col gap-3 items-center text-center">
          <p>{ps.doctor_display_name ?? "Your doctor"}&apos;s screen should show this same code:</p>
          <p className="text-6xl font-extrabold tracking-[0.3em] tabular-nums">{ps.code4}</p>
          <p className="text-sm text-neutral-400">Confirm only if it matches. Nothing is shared until you do.</p>
          <button type="button" disabled={busy} onClick={confirm} className="w-full rounded-lg bg-white text-black font-semibold py-3 disabled:opacity-40">
            {busy ? "Confirming…" : "It matches: start sharing"}
          </button>
        </div>
      ) : qr && ps.status !== "idle" ? (
        <div className="flex flex-col gap-3">
          <Qr text={qr.url} />
          <p className="text-sm text-neutral-300">
            Let your doctor scan this, or send them the link. It works once, for {Math.floor(left / 60)}:
            {String(left % 60).padStart(2, "0")} more.
          </p>
          {qr.demo && <p className="text-sm text-amber-300">Demo pairing: it receives demo cards only.</p>}
          <div className="flex gap-2">
            <button type="button" onClick={() => copy(qr.url)} className="flex-1 rounded-lg bg-neutral-800 py-2">
              {copied ? "Link copied" : "Copy link"}
            </button>
            <button type="button" onClick={() => setQr(null)} className="flex-1 rounded-lg bg-neutral-800 py-2">
              Stop
            </button>
          </div>
        </div>
      ) : ps.status === "awaiting_scan" ? (
        <p className="text-sm text-neutral-300">A pairing code is open on your Irin&apos;s screen or another phone. An unused code expires on its own.</p>
      ) : qr ? (
        <p className="text-sm text-neutral-400">Starting…</p>
      ) : (
        <button type="button" disabled={busy} onClick={start} className="rounded-lg bg-white text-black font-semibold py-3 disabled:opacity-40">
          Share with my doctor
        </button>
      )}
      {!qr && !joined && demo && ps.status === "idle" && (
        <p className="text-xs text-neutral-500">In demo mode a new pairing is a demo pairing: it receives demo cards only.</p>
      )}
      {msg && <p className="text-sm text-amber-300">{msg}</p>}
    </section>
  );
}
