import { useEffect, useState } from "react";
import SignalCard from "../components/SignalCard";
import { compareCards, listCards, type CardPair, type CardRecord } from "../lib/cards";
import type { SignalCard as Card } from "../lib/contracts";
import { useDevice } from "../lib/device";
import standing from "../fixtures/signal_card_standing.json";
import step from "../fixtures/signal_card_step.json";
import { readPairingState } from "../lib/pairing";
import RecallCards from "./device/RecallCards";
import DoctorSharing from "./rounds/DoctorSharing";
import StepTimeline from "./rounds/StepTimeline";
import WatchToday from "./rounds/WatchToday";

// Irin Rounds tab (justin.md R3): this morning's recall questions, then "What
// my doctor sees": the cards the device actually sealed (GET /api/rounds/cards,
// refreshed on card_sent), each with who received it. Until the first real
// card exists it shows the two SAMPLE cards, labeled "Sample card" and DEMO.
// At the very top, My doctor: the blue Share with doctor button (QR front and
// centre), pairing status, Revoke. Where the Pi serves
// /api/rounds/cards/compare, each kind is drawn twice: with the bedside device
// and phone only (brain-only), side by side on wide screens; an older Pi
// (404) gets the single cards.
const SAMPLES = [
  { key: "standing", label: "Basal Check", card: standing as unknown as Card },
  { key: "step", label: "Step check", card: step as unknown as Card },
] as const;

const KIND: Record<string, string> = {
  basal_check: "Basal Check", hypo_response: "Hypo Response", follow_up: "Follow-up", early_check: "Early check",
  step_check: "Step check", step_gate: "Step gate", safety: "Safety", graduation: "Graduation", baseline_note: "Baseline note",
};
/** "Jan 15, 07:05" from a naive Pi timestamp, read as text */
function when(iso: string | null | undefined): string {
  if (!iso) return "";
  const M = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return y ? `${M[m - 1]} ${d}, ${iso.slice(11, 16)}` : "";
}

function Delivery({ rec, names }: { rec: CardRecord; names: Map<string, string> }) {
  const at = when(rec.card.generated_at ?? rec.stored_at);
  if (rec.status === "sent") {
    const to = rec.recipients.map((id) => names.get(id) ?? "your doctor").join(", ");
    return <p className="text-sm text-emerald-400">Sent to {to}{at ? ` · ${at}` : ""}</p>;
  }
  if (rec.status === "unsent") return <p className="text-sm text-amber-300">Not delivered yet: your Irin keeps retrying{at ? ` · ${at}` : ""}</p>;
  if (rec.status === "no_recipient") return <p className="text-sm text-neutral-400">Not sent: no doctor is paired{at ? ` · ${at}` : ""}</p>;
  return <p className="text-sm text-neutral-400">{rec.status}{at ? ` · ${at}` : ""}</p>;
}

function Samples() {
  const [which, setWhich] = useState<(typeof SAMPLES)[number]["key"]>("standing");
  const sample = SAMPLES.find((s) => s.key === which) ?? SAMPLES[0];
  return (
    <>
      <p className="text-sm text-neutral-400">
        Your Irin has not sent your doctor a card yet. These are sample cards, shown exactly as your doctor&apos;s inbox
        draws them.
      </p>
      <div className="flex gap-2">
        {SAMPLES.map((s) => (
          <button
            key={s.key}
            type="button"
            aria-pressed={which === s.key}
            onClick={() => setWhich(s.key)}
            className={`px-3 py-1.5 rounded-full text-sm ${
              which === s.key ? "bg-white text-black font-semibold" : "bg-neutral-900 text-neutral-300"
            }`}
          >
            {s.label}
          </button>
        ))}
      </div>
      <SignalCard card={sample.card} sample />
    </>
  );
}

function Compare({ pairs, names }: { pairs: CardPair[]; names: Map<string, string> }) {
  const side = (label: string, card: Card) => (
    <div className="flex flex-col gap-2 min-w-0">
      <h4 className="text-sm font-semibold text-neutral-300">{label}</h4>
      <SignalCard card={card} />
    </div>
  );
  return (
    <div className="flex flex-col gap-6 lg:w-[min(64rem,calc(100vw-2rem))] lg:relative lg:left-1/2 lg:-translate-x-1/2">
      <p className="text-sm text-neutral-400">
        Same nights, same numbers where they can be known; without the bedside device, alarm and presence rows are
        reported or inferred instead of measured.
      </p>
      {pairs.map((p) => (
        <section key={`${p.kind}-${p.bedside.card.card_id}`} className="flex flex-col gap-3" aria-label={KIND[p.kind] ?? p.kind}>
          <h3 className="text-lg font-semibold">{KIND[p.kind] ?? p.kind}</h3>
          <Delivery rec={p.bedside} names={names} />
          <div className="grid gap-4 lg:grid-cols-2">
            {side("With the bedside device", p.bedside.card)}
            {side("Without the bedside device (phone only)", p.brain)}
          </div>
        </section>
      ))}
    </div>
  );
}

export default function RoundsTab() {
  const { target, socket } = useDevice();
  const snap = socket.snapshot;
  const base = target.status === "ready" ? target.url : null;
  const [cards, setCards] = useState<CardRecord[] | null>(null);
  const [pairs, setPairs] = useState<CardPair[] | null>(null);
  const [error, setError] = useState(false);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    if (!base) return;
    let alive = true;
    compareCards(base)
      .catch(() => null)
      .then(async (p) => {
        const c = p ? p.map((x) => x.bedside) : await listCards(base);
        if (!alive) return;
        setPairs(p);
        setCards(c);
        setError(false);
      })
      .catch(() => alive && setError(true));
    return () => {
      alive = false;
    };
  }, [base, socket.cardsVersion]);

  const names = new Map(readPairingState(snap?.pairing_state).pairings.map((p) => [p.doctor_id, p.doctor_display_name]));
  const real = cards ?? [];
  const latest = real[0];

  return (
    <section className="flex flex-col gap-4">
      {base && <DoctorSharing />}
      {base && <RecallCards items={socket.recallDue} baseUrl={base} demo={snap?.mode === "replay"} />}
      {base && snap && <WatchToday snap={snap} baseUrl={base} planState={socket.planState} />}
      {base && snap && <StepTimeline snap={snap} baseUrl={base} planState={socket.planState} />}
      <h2 className="text-xl font-semibold">What my doctor sees</h2>
      {error && <p className="text-sm text-red-400">Could not load the cards from your Irin.</p>}
      {!base || cards === null || real.length === 0 ? (
        base && cards === null && !error ? <p className="text-neutral-400">Loading…</p> : <Samples />
      ) : pairs ? (
        <Compare pairs={pairs} names={names} />
      ) : (
        <>
          <Delivery rec={latest} names={names} />
          <SignalCard card={latest.card} />
          {real.length > 1 && (
            <div className="flex flex-col gap-2">
              <h3 className="text-sm uppercase tracking-wider text-neutral-400">Earlier cards</h3>
              <ul className="flex flex-col gap-2">
                {real.slice(1).map((r) => (
                  <li key={`${r.card.card_id}-${r.stored_at}`} className="flex flex-col gap-2">
                    <button
                      type="button"
                      onClick={() => setOpen(open === r.card.card_id ? null : r.card.card_id)}
                      className="w-full flex items-center gap-2 bg-neutral-900 rounded-lg px-4 py-3 text-left"
                    >
                      <span className="flex-1 font-medium">{KIND[r.card.kind] ?? r.card.kind}</span>
                      {r.card.is_demo && <span className="bg-amber-400 text-black text-xs font-bold px-2 py-0.5 rounded">DEMO</span>}
                      <span className="text-sm text-neutral-400">{when(r.card.generated_at ?? r.stored_at)}</span>
                    </button>
                    {open === r.card.card_id && (
                      <>
                        <Delivery rec={r} names={names} />
                        <SignalCard card={r.card} />
                      </>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </section>
  );
}
