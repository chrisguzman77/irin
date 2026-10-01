import { useEffect, useRef, useState } from "react";
import { PinRejected } from "../../lib/api";
import { answerMatch, findMatches, type MatchCard, type MatchState, type Result } from "../../lib/buddy";

// B3+: "Find a buddy" (up to three suggestions from the relay's deterministic
// score; the intro and why lines are relay text, shown as given) and my
// matches (buddy_state.matches + hub_update "match"). Once both sides accept
// and the other side's Irin has handed over its pair_url, "Watch <name> from
// this phone" opens that watcher /pair link. Nothing here carries a glucose
// value, a place, or a contact detail (invariant 15). Phone-only accounts
// (Phase 1): `client` swaps the Pi calls for the relay's (lib/phoneBuddy.ts).
export interface MatchClient {
  find: () => Promise<Result<MatchCard[]>>;
  answer: (matchId: string, verb: "accept" | "decline") => Promise<Result<{ match_id: string; status: string }>>;
}
/** this phone's own answers, by match_id (`initialAnswered` seeds them, e.g. from a stored accept) */
export type Answered = Record<string, { status: string; mine: "accept" | "decline"; first_name: string }>;
const badge = "bg-amber-400 text-black text-xs font-bold px-2 py-0.5 rounded";
const btn = "flex-1 rounded-lg py-2 font-semibold disabled:opacity-40";

const sampleBadge = "border border-neutral-500 text-neutral-300 text-xs px-2 py-0.5 rounded";

/** One match offer; `children` (Accept / Decline) is left out on the Buddy home's "Your Buddy". */
export function MatchCardView({ card: c, demo, children }: { card: MatchCard; demo: boolean; children?: React.ReactNode }) {
  return (
    <article className="rounded-lg bg-neutral-900 p-3 flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-lg font-semibold">{c.first_name}</span>
        <span className="text-xs rounded-full border border-sky-600 text-sky-200 px-2 py-0.5">
          {c.mirror ? "mirror (opposite time zone)" : "twin"}
        </span>
        {(demo || c.is_demo) && <span className={badge}>DEMO</span>}
        {c.sample && <span className={sampleBadge}>Sample profile</span>}
      </div>
      {c.intro && <p className="text-sm text-neutral-200">{c.intro}</p>}
      {c.why && <p className="text-sm text-neutral-400 border-l-2 border-sky-500 pl-3">{c.why}</p>}
      <p className="text-xs text-neutral-500">
        Awake about {Math.round(c.hours_covered * 10) / 10} h of your night
        {c.shared_languages.length > 0 ? ` · speaks ${c.shared_languages.join(", ")}` : ""}
      </p>
      {children}
    </article>
  );
}

/** autoFind: the wizard's step 6 searches once on mount. onAccepted: called with
 * the suggestion's card once this phone's accept went through (Buddy v3). */
export default function FindBuddy({ base, matches, demo, autoFind = false, onAccepted, client, initialAnswered }: {
  base: string; matches: MatchState[]; demo: boolean; autoFind?: boolean; onAccepted?: (card: MatchCard) => void; client?: MatchClient;
  initialAnswered?: Answered;
}) {
  const api: MatchClient = client ?? { find: () => findMatches(base), answer: (id, verb) => answerMatch(base, id, verb) };
  const [cards, setCards] = useState<MatchCard[] | null>(null);
  // this phone's own answers, until the snapshot / hub_update says more
  const [answered, setAnswered] = useState<Answered>(() => initialAnswered ?? {});
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const run = async <T,>(f: () => Promise<T>): Promise<T | undefined> => {
    setBusy(true);
    setMsg("");
    try {
      return await f();
    } catch (e) {
      if (!(e instanceof PinRejected)) setMsg("Could not reach your Irin.");
    } finally {
      setBusy(false);
    }
  };

  const find = () =>
    run(async () => {
      const r = await api.find();
      if (r.ok) setCards(r.value);
      else setMsg(r.reason);
    });

  const started = useRef(false);
  useEffect(() => {
    if (autoFind && !started.current) {
      started.current = true;
      void find();
    }
  });

  const answer = (id: string, first_name: string, verb: "accept" | "decline") =>
    run(async () => {
      const r = await api.answer(id, verb);
      if (!r.ok) return setMsg(r.reason);
      setAnswered((a) => ({ ...a, [id]: { status: r.value.status, mine: verb, first_name } }));
      const card = cards?.find((c) => c.match_id === id);
      if (verb === "accept" && card && r.value.status === "accepted") onAccepted?.(card);
    });

  // snapshot rows win on status and pair_url; local answers fill the gap
  const rows = new Map<string, MatchState>(matches.map((m) => [m.match_id, m]));
  for (const [id, a] of Object.entries(answered)) {
    const m = rows.get(id);
    const status = m && m.status !== "offered" ? m.status : a.mine === "decline" ? "declined" : m?.status ?? a.status;
    rows.set(id, { match_id: id, first_name: m?.first_name ?? a.first_name, status, pair_url: m?.pair_url ?? null, sample: m?.sample ?? false });
  }
  // an unanswered suggestion shows as its full card, and only there
  const suggestions = (cards ?? []).filter((c) => !answered[c.match_id] && (rows.get(c.match_id)?.status ?? "offered") === "offered");
  const shown = new Set(suggestions.map((c) => c.match_id));
  const mine = [...rows.values()]
    .filter((m) => !shown.has(m.match_id))
    .map((m) => {
      const c = cards?.find((x) => x.match_id === m.match_id);
      return { ...m, first_name: m.first_name ?? c?.first_name ?? "your match", sample: m.sample || !!c?.sample };
    });

  return (
    <section className="rounded-xl border border-neutral-800 px-4 py-3 flex flex-col gap-3">
      <h3 className="text-xs uppercase tracking-wider text-neutral-400 flex items-center gap-2">
        Find a buddy {demo && <span className={badge}>DEMO</span>}
      </h3>
      <button type="button" disabled={busy} className="rounded-lg px-3 py-2 bg-sky-400 text-black font-semibold disabled:opacity-40" onClick={find}>
        {cards ? "Look again" : "Find a buddy"}
      </button>
      {busy && !cards && <p className="text-sm text-neutral-300 animate-pulse">Finding your buddy…</p>}
      {cards && suggestions.length === 0 && <p className="text-sm text-neutral-400">No new suggestions right now.</p>}
      {suggestions.map((c) => (
        <MatchCardView key={c.match_id} card={c} demo={demo}>
          <div className="flex gap-2">
            <button type="button" disabled={busy} className={`${btn} bg-neutral-800`} onClick={() => answer(c.match_id, c.first_name, "decline")}>
              Decline
            </button>
            <button type="button" disabled={busy} className={`${btn} bg-sky-400 text-black`} onClick={() => answer(c.match_id, c.first_name, "accept")}>
              Accept
            </button>
          </div>
        </MatchCardView>
      ))}

      {mine.length > 0 && <h4 className="text-xs uppercase tracking-wider text-neutral-400 pt-1">My matches</h4>}
      {mine.map((m) => (
        <div key={m.match_id} className="flex flex-col gap-2 rounded-lg bg-neutral-900 p-3">
          <div className="flex items-center gap-2">
            <span className="font-semibold">{m.first_name}</span>
            {demo && <span className={badge}>DEMO</span>}
            {m.sample && <span className={sampleBadge}>Sample profile</span>}
          </div>
          {m.status === "declined" ? (
            <p className="text-sm text-neutral-500">Declined.</p>
          ) : m.status === "accepted" ? (
            m.sample ? (
              <p className="text-sm text-neutral-300">Sample profile: no watch link.</p>
            ) : m.pair_url ? (
              <a href={m.pair_url} target="_blank" rel="noopener noreferrer"
                className="rounded-lg px-3 py-2 bg-sky-400 text-black font-semibold text-center">
                Watch {m.first_name} from this phone
              </a>
            ) : (
              <p className="text-sm text-neutral-300">Both accepted. Waiting for {m.first_name}'s Irin to send the watch link.</p>
            )
          ) : answered[m.match_id]?.mine === "accept" ? (
            <p className="text-sm text-amber-300">Waiting for {m.first_name} to accept.</p>
          ) : (
            <div className="flex gap-2">
              <button type="button" disabled={busy} className={`${btn} bg-neutral-800`} onClick={() => answer(m.match_id, m.first_name, "decline")}>
                Decline
              </button>
              <button type="button" disabled={busy} className={`${btn} bg-sky-400 text-black`} onClick={() => answer(m.match_id, m.first_name, "accept")}>
                Accept
              </button>
            </div>
          )}
        </div>
      ))}
      {msg && <p role="status" className="text-sm text-red-400">{msg}</p>}
    </section>
  );
}
