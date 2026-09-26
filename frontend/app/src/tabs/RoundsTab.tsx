import { useState } from "react";
import SignalCard from "../components/SignalCard";
import type { SignalCard as Card } from "../lib/contracts";
import standing from "../fixtures/signal_card_standing.json";
import step from "../fixtures/signal_card_step.json";

// Irin Rounds tab (justin.md R3). Built so far: "What my doctor sees", the
// card renderer shared with the clinician inbox. Until the device sends real
// cards (Chris's R-steps and the relay) it shows the two SAMPLE cards
// (backend/tests/fixtures), labeled "Sample card" and DEMO. Pairing status,
// check-in status, glucagon, and the step timeline arrive with their endpoints.
const SAMPLES = [
  { key: "standing", label: "Basal Check", card: standing as unknown as Card },
  { key: "step", label: "Step check", card: step as unknown as Card },
] as const;

export default function RoundsTab() {
  const [which, setWhich] = useState<(typeof SAMPLES)[number]["key"]>("standing");
  const sample = SAMPLES.find((s) => s.key === which) ?? SAMPLES[0];
  return (
    <section className="flex flex-col gap-4">
      <div>
        <h2 className="text-xl font-semibold">What my doctor sees</h2>
        <p className="text-sm text-neutral-400">
          Your Irin has not sent your doctor a card yet. These are sample cards, shown exactly as your doctor&apos;s inbox
          draws them.
        </p>
      </div>
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
      <p className="text-xs text-neutral-500">
        Pairing with your doctor, today&apos;s check-in, and the step timeline arrive with the Rounds backend.
      </p>
    </section>
  );
}
