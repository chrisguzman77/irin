import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import type { Settings } from "../../lib/contracts";
import { PinRejected } from "../../lib/api";
import {
  cancelVoice, confirmVoice, logTreatment, recentTreatments, sendVoice,
  type Treatment, type VoiceReply,
} from "../../lib/log";

// Step 6: logging + voice. The ECHO + CONFIRM screen is sacred: insulin is
// only ever saved from its Confirm button, never automatically, and Cancel
// or a timeout saves nothing.

// Web Speech API (Chrome/Safari prefix it; Firefox has none -> typing only).
interface Recognition {
  lang: string;
  interimResults: boolean;
  maxAlternatives: number;
  onresult: ((e: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
}
type RecognitionCtor = new () => Recognition;
const SpeechRecognition: RecognitionCtor | undefined =
  (window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor })
    .SpeechRecognition ??
  (window as unknown as { webkitSpeechRecognition?: RecognitionCtor }).webkitSpeechRecognition;

/** What the echo screen is confirming: a pending voice entry on the Pi, or a
 * form entry that is sent only after Confirm. */
type Pending =
  | { kind: "voice"; pendingId: string; echo: string }
  | { kind: "form"; echo: string; entries: Omit<Treatment, "timestamp">[] };

const input = "bg-neutral-900 border border-neutral-700 rounded-lg px-3 py-2 text-white w-full";
const btn = "rounded-lg px-4 py-3 font-semibold disabled:opacity-40";

function Card({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="border border-neutral-800 rounded-xl p-4 mb-4">
      <h3 className="text-xs uppercase tracking-wider text-neutral-400 mb-3">{title}</h3>
      {children}
    </section>
  );
}

const fmtTime = (iso: string) =>
  new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });

function describe(t: Treatment): string {
  switch (t.kind) {
    case "carbs": return `${t.carbs_g} g carbs`;
    case "bolus": return `${t.insulin_units} units bolus`;
    case "basal": return `${t.insulin_units} units basal`;
    case "note": return `note: ${t.text ?? ""}`;
    default: return t.kind;
  }
}

const positive = (s: string) => {
  const n = Number(s);
  return s.trim() !== "" && Number.isFinite(n) && n > 0 ? n : null;
};

export default function LogView({ baseUrl, settings }: { baseUrl: string; settings: Settings | undefined }) {
  const [text, setText] = useState("");
  const [listening, setListening] = useState(false);
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ tone: "ok" | "ask" | "err"; text: string } | null>(null);
  const [recent, setRecent] = useState<Treatment[]>([]);
  const [basal, setBasal] = useState(settings?.basal_units != null ? String(settings.basal_units) : "");
  const [carbs, setCarbs] = useState("");
  const [units, setUnits] = useState("");
  const [note, setNote] = useState("");
  const rec = useRef<Recognition | null>(null);

  const refresh = useCallback(async () => {
    try {
      setRecent((await recentTreatments(baseUrl)).slice().reverse());
    } catch {
      /* the list is a convenience; the live view shows connection problems */
    }
  }, [baseUrl]);
  useEffect(() => {
    refresh();
  }, [refresh]);

  const fail = (e: unknown) => {
    if (e instanceof PinRejected) return; // the gate takes over
    setMsg({ tone: "err", text: e instanceof Error ? e.message : "Something went wrong." });
  };

  const handleVoice = async (reply: VoiceReply, spoken: string) => {
    switch (reply.status) {
      case "stored":
        setMsg({ tone: "ok", text: `Saved: ${reply.stored.map(describe).join(", ")}` });
        setText("");
        refresh();
        break;
      case "needs_confirm":
        setPending({ kind: "voice", pendingId: reply.pending_id, echo: reply.echo });
        break;
      case "incomplete":
        // The partial parse asks for the missing piece; the entry stays in the box to complete.
        setText(spoken);
        setMsg({ tone: "ask", text: `Your Irin asks: ${reply.ask} Add it and send again.` });
        break;
      case "error":
        setMsg({ tone: "err", text: reply.error });
        break;
      default:
        setMsg({ tone: "err", text: "Nothing was saved." });
    }
  };

  const submitText = async (spoken: string) => {
    if (!spoken.trim() || busy) return;
    setBusy(true);
    setMsg(null);
    try {
      await handleVoice(await sendVoice(baseUrl, spoken.trim()), spoken.trim());
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  };

  const listen = () => {
    if (!SpeechRecognition || listening) return;
    const r = new SpeechRecognition();
    r.lang = "en-US";
    r.interimResults = false;
    r.maxAlternatives = 1;
    r.onresult = (e) => {
      const said = e.results[0]?.[0]?.transcript ?? "";
      setText(said);
      submitText(said);
    };
    r.onerror = (e) => setMsg({ tone: "err", text: `Microphone: ${e.error}` });
    r.onend = () => setListening(false);
    rec.current = r;
    setListening(true);
    setMsg(null);
    r.start();
  };

  // --- the echo screen's two exits ---
  const confirm = async () => {
    if (!pending || busy) return;
    setBusy(true);
    try {
      if (pending.kind === "voice") {
        const reply = await confirmVoice(baseUrl, pending.pendingId);
        if (reply.status === "stored") {
          setMsg({ tone: "ok", text: `Saved: ${reply.stored.map(describe).join(", ")}` });
          setText("");
        } else {
          setMsg({ tone: "err", text: "That took too long, so nothing was saved. Please log it again." });
        }
      } else {
        for (const entry of pending.entries) await logTreatment(baseUrl, entry);
        setMsg({ tone: "ok", text: `Saved: ${pending.echo}` });
        setCarbs("");
        setUnits("");
      }
      refresh();
    } catch (e) {
      fail(e);
    } finally {
      setPending(null);
      setBusy(false);
    }
  };
  const cancel = async () => {
    if (!pending) return;
    const p = pending;
    setPending(null);
    setMsg({ tone: "ask", text: "Cancelled. Nothing was saved." });
    if (p.kind === "voice") {
      try {
        await cancelVoice(baseUrl, p.pendingId);
      } catch {
        /* an un-cancelled entry still expires on the Pi after its timeout */
      }
    }
  };

  // --- forms ---
  const basalUnits = positive(basal);
  const carbsG = positive(carbs);
  const bolusUnits = positive(units);
  const unitsTyped = units.trim() !== "";

  const logBasal = () => {
    if (basalUnits === null) return;
    setPending({
      kind: "form",
      echo: `${basalUnits} units basal, taken now`,
      entries: [{ kind: "basal", insulin_units: basalUnits, confirmed: true }],
    });
  };
  const logMeal = async () => {
    if (carbsG === null || (unitsTyped && bolusUnits === null)) return;
    const carbsEntry: Omit<Treatment, "timestamp"> = { kind: "carbs", carbs_g: carbsG, confirmed: true };
    if (bolusUnits === null) {
      // carbs alone: no insulin, nothing to confirm
      setBusy(true);
      try {
        await logTreatment(baseUrl, carbsEntry);
        setMsg({ tone: "ok", text: `Saved: ${carbsG} g carbs` });
        setCarbs("");
        refresh();
      } catch (e) {
        fail(e);
      } finally {
        setBusy(false);
      }
      return;
    }
    setPending({
      kind: "form",
      echo: `${carbsG} g carbs and ${bolusUnits} units bolus`,
      entries: [carbsEntry, { kind: "bolus", insulin_units: bolusUnits, confirmed: true }],
    });
  };
  const logNote = async () => {
    if (!note.trim()) return;
    setBusy(true);
    try {
      await logTreatment(baseUrl, { kind: "note", text: note.trim().slice(0, 500), confirmed: true });
      setMsg({ tone: "ok", text: "Note saved" });
      setNote("");
      refresh();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  };

  if (pending) {
    // The echo screen. No default button, no timer that confirms; Confirm is the only way in.
    return (
      <section role="dialog" aria-label="Confirm entry" className="flex flex-col items-center gap-6 py-10 text-center">
        <p className="text-neutral-400 uppercase tracking-wider text-sm">Check before saving</p>
        <p className="text-3xl font-bold leading-snug">{pending.echo}</p>
        <p className="text-neutral-400">Is this right?</p>
        <div className="flex w-full gap-3">
          <button type="button" className={`${btn} flex-1 border border-neutral-600 text-white text-lg`} onClick={cancel} disabled={busy}>
            Cancel
          </button>
          <button type="button" className={`${btn} flex-1 bg-amber-400 text-black text-lg`} onClick={confirm} disabled={busy}>
            Confirm
          </button>
        </div>
        {pending.kind === "voice" && (
          <p className="text-xs text-neutral-500">A spoken entry is discarded if it is not confirmed within a few seconds.</p>
        )}
      </section>
    );
  }

  return (
    <div className="pb-8">
      {msg && (
        <p
          role="status"
          className={`mb-4 rounded-lg px-3 py-2 text-sm font-semibold ${
            msg.tone === "ok" ? "bg-emerald-900 text-emerald-100" : msg.tone === "ask" ? "bg-amber-900 text-amber-100" : "bg-red-900 text-red-100"
          }`}
        >
          {msg.text}
        </p>
      )}

      <Card title="Say it">
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            submitText(text);
          }}
        >
          {SpeechRecognition && (
            <button type="button" onClick={listen} disabled={busy || listening}
              className={`${btn} text-lg ${listening ? "bg-red-600 text-white" : "bg-white text-black"}`}>
              {listening ? "Listening…" : "🎤︎ Tap and speak"}
            </button>
          )}
          <div className="flex gap-2">
            <input className={input} value={text} maxLength={200} onChange={(e) => setText(e.target.value)}
              placeholder="log 45 carbs and 5 units" aria-label="What to log" />
            <button type="submit" className={`${btn} bg-neutral-800 text-white`} disabled={busy || !text.trim()}>
              Send
            </button>
          </div>
          {!SpeechRecognition && <p className="text-xs text-neutral-500">This browser has no speech input; type it instead.</p>}
        </form>
      </Card>

      <Card title="Basal taken">
        <div className="flex gap-2 items-end">
          <label className="flex-1 flex flex-col gap-1">
            <span className="text-sm text-neutral-300">Units</span>
            <input className={input} type="number" inputMode="decimal" step="0.5" value={basal} onChange={(e) => setBasal(e.target.value)} />
          </label>
          <button type="button" className={`${btn} bg-amber-400 text-black`} disabled={busy || basalUnits === null} onClick={logBasal}>
            Log basal
          </button>
        </div>
      </Card>

      <Card title="Meal">
        <div className="grid grid-cols-2 gap-2">
          <label className="flex flex-col gap-1">
            <span className="text-sm text-neutral-300">Carbs (g)</span>
            <input className={input} type="number" inputMode="numeric" value={carbs} onChange={(e) => setCarbs(e.target.value)} />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-sm text-neutral-300">Insulin (units, optional)</span>
            <input className={input} type="number" inputMode="decimal" step="0.5" value={units} onChange={(e) => setUnits(e.target.value)} />
          </label>
        </div>
        <button type="button" className={`${btn} mt-3 w-full bg-amber-400 text-black`}
          disabled={busy || carbsG === null || (unitsTyped && bolusUnits === null)} onClick={logMeal}>
          Log meal
        </button>
      </Card>

      <Card title="Note">
        <div className="flex gap-2">
          <input className={input} value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} placeholder="e.g. went for a run" />
          <button type="button" className={`${btn} bg-neutral-800 text-white`} disabled={busy || !note.trim()} onClick={logNote}>
            Save
          </button>
        </div>
      </Card>

      <Card title="Last 24 hours">
        {recent.length === 0 ? (
          <p className="text-neutral-500 text-sm">Nothing logged yet.</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {recent.map((t, i) => (
              <li key={`${t.timestamp}-${i}`} className="flex justify-between text-sm">
                <span className="text-neutral-200">{describe(t)}</span>
                <span className="text-neutral-500 tabular-nums">{fmtTime(t.timestamp)}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
