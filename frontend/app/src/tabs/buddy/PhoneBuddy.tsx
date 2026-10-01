import { useCallback, useEffect, useMemo, useState } from "react";
import { clearAccount, signUpPhone, useAccount } from "../../lib/account";
import type { BuddyProfile, MatchCard, MatchState } from "../../lib/buddy";
import {
  acceptedIds, answerMatch, claimHub, findMatches, forgetAccepted, getHub, getMatches, getMe, profileOf, rememberAccepted, updateMe,
  type Me, type PhoneMatch,
} from "../../lib/phoneBuddy";
import BuddyWizard from "./BuddyWizard";
import ConnectCgm from "./ConnectCgm";
import FindBuddy, { MatchCardView, type Answered, type MatchClient } from "./FindBuddy";
import HubScreen, { type HubClient } from "./HubScreen";

// Phone-only accounts, Phase 1 (relay/README.md "Phone-only accounts"): the
// Irin Buddy tab with NO device paired. No account -> the wizard signs up on
// the relay; then "Connect your CGM" verifies the feed through Irin Cloud;
// verified -> Find a buddy, the My buddy card (the newest accepted row of
// GET /v0/users/matches, re-read every 60 s so a real buddy's acceptance
// shows up), the opt-ins, and the hub, all against the relay directly. Being
// WATCHED needs an Irin (a device seals listings and alerts): the copy says
// so, and hub_watchable stays off. With a device paired BuddyTab never
// renders this (the Pi path is unchanged).
type OptIns = BuddyProfile["optins"];
const OPT_INS: [keyof OptIns, string, string][] = [
  ["have_buddy", "I want a buddy", "One paired T1D adult is the last human rung of your alarm ladder."],
  ["be_watcher", "I'll watch over my buddy", "Your phone can be alerted when your buddy's low goes unanswered."],
  ["hub_watchable", "List me on the hub", "If nobody answers, a pseudonymous listing asks verified volunteers for help. No glucose value, place, or number is ever shown."],
  ["hub_volunteer", "I volunteer on the hub", "You may see listings from people you have never met and claim one."],
];
const NO_IRIN = "Without an Irin your buddy cannot be alerted yet.";
const MATCHES_REFRESH_MS = 60_000;
const hubClient: HubClient = { list: getHub, claim: claimHub };

function Box({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-neutral-800 px-4 py-3 flex flex-col gap-2">
      <h3 className="text-xs uppercase tracking-wider text-neutral-400">{title}</h3>
      {children}
    </section>
  );
}

/** The four opt-ins, each a PUT /v0/users/me of the full body; hub_watchable is disabled here (no Irin to list from). */
function PhoneOptIns({ me, onSaved }: { me: Me; onSaved: (me: Me) => void }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null);
  const flip = async (k: keyof OptIns) => {
    if (busy) return;
    setBusy(true);
    setMsg(null);
    const r = await updateMe({ ...profileOf(me), optins: { ...me.optins, [k]: !me.optins[k] } });
    setBusy(false);
    if (r.ok) {
      onSaved(r.value);
      setMsg({ text: "Saved.", ok: true });
    } else setMsg({ text: r.reason, ok: false });
  };
  return (
    <Box title="Night Buddy opt-ins">
      {OPT_INS.map(([k, label, about]) => (
        <label key={k} className="flex items-start justify-between gap-3 py-1">
          <span className="flex flex-col">
            <span className="text-sm text-neutral-200">{label}</span>
            <span className="text-xs text-neutral-500">{k === "hub_watchable" ? `${NO_IRIN} ${about}` : about}</span>
          </span>
          <input type="checkbox" className="size-6 shrink-0 accent-sky-400" checked={me.optins[k]}
            disabled={busy || k === "hub_watchable"} onChange={() => flip(k)} />
        </label>
      ))}
      <p className="text-xs text-neutral-500">Four separate choices, all off until you turn them on; turning one off takes effect at once.</p>
      {msg && <p role="status" className={`text-sm ${msg.ok ? "text-emerald-400" : "text-red-400"}`}>{msg.text}</p>}
    </Box>
  );
}

function PhoneHubEntry({ me, onSaved, onOpen }: { me: Me; onSaved: (me: Me) => void; onOpen: () => void }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  if (me.optins.hub_volunteer)
    return (
      <button type="button" className="rounded-lg px-3 py-3 bg-sky-400 text-black font-semibold" onClick={onOpen}>
        Go to Hub
      </button>
    );
  const join = async () => {
    setBusy(true);
    setMsg("");
    const r = await updateMe({ ...profileOf(me), optins: { ...me.optins, hub_volunteer: true } });
    setBusy(false);
    if (r.ok) onSaved(r.value);
    else setMsg(r.reason);
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

function forgetAccount() {
  forgetAccepted();
  clearAccount();
}

function ForgetAccount({ username }: { username: string }) {
  const [ask, setAsk] = useState(false);
  return (
    <Box title="This phone">
      <p className="text-sm text-neutral-300">Signed up as {username}. There is no password: a phone that forgets this account signs up again.</p>
      {!ask ? (
        <button type="button" className="self-start rounded-lg px-3 py-2 border border-red-900 text-red-400" onClick={() => setAsk(true)}>
          Forget this account on this phone
        </button>
      ) : (
        <div className="flex gap-2">
          <button type="button" className="flex-1 rounded-lg px-3 py-2 bg-red-500 text-black font-semibold" onClick={forgetAccount}>
            Yes, forget it
          </button>
          <button type="button" className="rounded-lg px-3 py-2 border border-neutral-700 text-neutral-300" onClick={() => setAsk(false)}>
            Cancel
          </button>
        </div>
      )}
    </Box>
  );
}

export default function PhoneBuddy() {
  const account = useAccount();
  const [me, setMe] = useState<Me | null>(null);
  const [meErr, setMeErr] = useState("");
  const [view, setView] = useState<"home" | "hub" | "edit">("home");
  const [matches, setMatches] = useState<PhoneMatch[] | null>(null);
  const [matchErr, setMatchErr] = useState("");

  // GET /v0/users/me on load (and after sign-up / verify): refreshes verified
  // and the opt-ins; a 401 clears the account and this screen flips to sign-up.
  const userId = account?.user_id ?? null;
  const verified = account?.verified ?? false;
  useEffect(() => {
    if (!userId) {
      setMe(null);
      return;
    }
    let live = true;
    getMe().then((r) => {
      if (!live) return;
      if (r.ok) {
        setMe(r.value);
        setMeErr("");
      } else setMeErr(r.reason);
    });
    return () => {
      live = false;
    };
  }, [userId, verified]);

  // GET /v0/users/matches now, after every answer, and every 60 s while the
  // tab is open: a real buddy's acceptance arrives this way.
  const refreshMatches = useCallback(async () => {
    const r = await getMatches();
    if (r.ok) {
      setMatches(r.value);
      setMatchErr("");
    } else setMatchErr(r.reason);
  }, []);
  useEffect(() => {
    if (!userId || !verified) {
      setMatches(null);
      return;
    }
    void refreshMatches();
    const id = window.setInterval(() => void refreshMatches(), MATCHES_REFRESH_MS);
    return () => window.clearInterval(id);
  }, [userId, verified, refreshMatches]);

  const matchClient = useMemo<MatchClient>(
    () => ({
      find: findMatches,
      answer: async (id, verb) => {
        const r = await answerMatch(id, verb);
        if (r.ok) {
          if (verb === "accept") rememberAccepted(id);
          void refreshMatches();
        }
        return r;
      },
    }),
    [refreshMatches],
  );

  const signUp = useMemo(
    () => ({ initial: null, save: (p: BuddyProfile) => signUpPhone(p), onSaved: () => undefined }),
    [],
  );
  const edit = useMemo(
    () =>
      me && {
        initial: profileOf(me),
        save: async (p: BuddyProfile) => {
          const r = await updateMe(p);
          if (r.ok) setMe(r.value);
          return r;
        },
        onSaved: () => setView("home"),
      },
    [me],
  );

  if (!account)
    return (
      <>
        <p className="text-sm text-neutral-300">
          No Irin paired. You can still sign up, connect your CGM, be matched, and volunteer on the hub. {NO_IRIN}
        </p>
        <BuddyWizard base="" demo={false} settings={undefined} matches={[]} onAccepted={() => undefined} phone={signUp} />
      </>
    );

  if (!account.verified) return <ConnectCgm onVerified={() => undefined} onStartOver={forgetAccount} />;

  if (view === "hub") return <HubScreen base="" demo={false} onBack={() => setView("home")} client={hubClient} />;

  if (view === "edit" && edit)
    return (
      <>
        <button type="button" className="self-start rounded-lg px-4 py-2 bg-neutral-800 text-neutral-200" onClick={() => setView("home")}>
          ← Cancel
        </button>
        <BuddyWizard base="" demo={false} settings={undefined} matches={[]} onAccepted={() => undefined} phone={edit} />
      </>
    );

  // the My buddy card is the newest accepted row (the relay lists newest last)
  const myBuddy: MatchCard | null = matches ? ([...matches].reverse().find((m) => m.status === "accepted") ?? null) : null;
  const rows: MatchState[] = (matches ?? []).map((m) => ({ match_id: m.match_id, first_name: m.first_name, status: m.status, pair_url: null, sample: m.sample }));
  // an "offered" row this phone already accepted reads as "Waiting for X to accept"
  const accepted = acceptedIds();
  const initialAnswered: Answered = Object.fromEntries(
    (matches ?? [])
      .filter((m) => m.status === "offered" && accepted.has(m.match_id))
      .map((m) => [m.match_id, { status: "offered", mine: "accept" as const, first_name: m.first_name }]),
  );

  return (
    <>
      <p className="text-sm text-neutral-300">CGM verified. {NO_IRIN} Pair one on the Device tab and this account links to it.</p>
      {myBuddy ? (
        <>
          <h3 className="text-lg font-semibold">Your Buddy</h3>
          <MatchCardView card={myBuddy} demo={false} />
          <p className="text-sm text-amber-300">{NO_IRIN}</p>
        </>
      ) : matches ? (
        // mounted once the matches are known, so the seeded answers are there from the start
        <FindBuddy base="" matches={rows} demo={false} autoFind={!!me?.optins.have_buddy} onAccepted={() => void refreshMatches()}
          client={matchClient} initialAnswered={initialAnswered} />
      ) : (
        !matchErr && <p className="text-sm text-neutral-400 animate-pulse">Loading your matches…</p>
      )}
      {matchErr && <p role="status" className="text-sm text-red-400">{matchErr}</p>}
      {me && <PhoneHubEntry me={me} onSaved={setMe} onOpen={() => setView("hub")} />}
      {me && <PhoneOptIns me={me} onSaved={setMe} />}
      {me && (
        <button type="button" className="self-start rounded-lg px-3 py-2 bg-neutral-900 text-neutral-200" onClick={() => setView("edit")}>
          Edit profile
        </button>
      )}
      {!me && !meErr && <p className="text-sm text-neutral-400 animate-pulse">Loading your profile…</p>}
      {meErr && <p role="status" className="text-sm text-red-400">{meErr}</p>}
      <ForgetAccount username={account.username} />
    </>
  );
}
