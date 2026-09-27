import { useEffect, useState } from "react";
import { PinRejected } from "../lib/api";
import { readBuddyState } from "../lib/buddy";
import type { Settings } from "../lib/contracts";
import { useDevice } from "../lib/device";
import { revokePairing } from "../lib/pairing";
import { applied, saveSettings } from "../lib/settings";
import TreatingButton from "./buddy/TreatingButton";

// Irin Buddy tab, B1: the buddy card (paired name, twin | mirror, revoke, this
// morning's line), the four opt-ins (all off by default, each revocable at
// once: invariant 16), the emergency-script editor, and the treating button
// while an alert is open. Everything renders from the snapshot; a setting
// shows as saved only when the Pi's settings_change carries it. A5 (Find a
// Buddy) is not built.
type OptIns = NonNullable<Settings["night_buddy"]>;
const OPT_INS: [keyof OptIns, string, string][] = [
  ["have_buddy", "I want a buddy", "One paired T1D adult is the last human rung of your alarm ladder."],
  ["be_watcher", "I'll watch over my buddy", "Your phone can be alerted when your buddy's low goes unanswered."],
  ["hub_watchable", "List me on the hub", "If nobody answers, a pseudonymous listing asks verified volunteers for help. No glucose value, place, or number is ever shown."],
  ["hub_volunteer", "I volunteer on the hub", "You may see listings from people you have never met and claim one."],
];
const OFF: OptIns = { have_buddy: false, be_watcher: false, hub_watchable: false, hub_volunteer: false };
const ECHO_WAIT_MS = 5000;

function Box({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-neutral-800 px-4 py-3 flex flex-col gap-2">
      <h3 className="text-xs uppercase tracking-wider text-neutral-400">{title}</h3>
      {children}
    </section>
  );
}

/** Save one patch; resolves to "" once sent, or the reason it was not. */
async function send(base: string, patch: Partial<Settings>): Promise<string> {
  try {
    const r = await saveSettings(base, patch);
    return r.ok ? "" : r.reason;
  } catch (e) {
    if (e instanceof PinRejected) return "";
    return "Could not reach your Irin. Nothing was saved.";
  }
}

/** Waits for the Pi to echo `pending`; the message says where it stands. */
function useEcho(current: Settings | undefined) {
  const [pending, setPending] = useState<Partial<Settings> | null>(null);
  const [msg, setMsg] = useState<{ text: string; tone: "ok" | "error" | "info" } | null>(null);
  useEffect(() => {
    if (pending && current && applied(current, pending)) {
      setPending(null);
      setMsg({ text: "Saved on your Irin.", tone: "ok" });
    }
  }, [current, pending]);
  useEffect(() => {
    if (!pending) return;
    const id = window.setTimeout(
      () => setMsg({ text: "Your Irin accepted the change but has not confirmed it yet. Check the connection.", tone: "error" }),
      ECHO_WAIT_MS,
    );
    return () => window.clearTimeout(id);
  }, [pending]);
  return { pending, setPending, msg, setMsg };
}

const tone = (t: "ok" | "error" | "info") => (t === "ok" ? "text-emerald-400" : t === "error" ? "text-red-400" : "text-amber-300");

function OptInToggles({ current, base }: { current: Settings; base: string }) {
  const opts = { ...OFF, ...(current.night_buddy ?? {}) };
  const { pending, setPending, msg, setMsg } = useEcho(current);
  const flip = async (k: keyof OptIns) => {
    if (pending) return;
    const patch: Partial<Settings> = { night_buddy: { ...opts, [k]: !opts[k] } };
    setMsg({ text: "Saving…", tone: "info" });
    const err = await send(base, patch);
    if (err) setMsg({ text: err, tone: "error" });
    else setPending(patch);
  };
  return (
    <Box title="Night Buddy opt-ins">
      {OPT_INS.map(([k, label, about]) => (
        <label key={k} className="flex items-start justify-between gap-3 py-1">
          <span className="flex flex-col">
            <span className="text-sm text-neutral-200">{label}</span>
            <span className="text-xs text-neutral-500">{about}</span>
          </span>
          <input
            type="checkbox"
            className="size-6 shrink-0 accent-sky-400"
            checked={opts[k]}
            disabled={!!pending}
            onChange={() => flip(k)}
          />
        </label>
      ))}
      <p className="text-xs text-neutral-500">Four separate choices, all off until you turn them on; turning one off takes effect at once.</p>
      {msg && <p role="status" className={`text-sm ${tone(msg.tone)}`}>{msg.text}</p>}
    </Box>
  );
}

function ScriptEditor({ current, base }: { current: Settings; base: string }) {
  const saved = current.emergency_script?.steps ?? [];
  const [steps, setSteps] = useState<string[]>(saved);
  const [dirty, setDirty] = useState(false);
  const { pending, setPending, msg, setMsg } = useEcho(current);
  useEffect(() => {
    if (!dirty) setSteps(current.emergency_script?.steps ?? []);
  }, [current, dirty]);
  useEffect(() => {
    if (pending === null && msg?.tone === "ok") setDirty(false);
  }, [pending, msg]);

  const edit = (next: string[]) => {
    setSteps(next);
    setDirty(true);
    setMsg(null);
  };
  const clean = steps.map((s) => s.trim()).filter(Boolean);
  const save = async () => {
    const patch: Partial<Settings> = { emergency_script: { steps: clean } };
    setMsg({ text: "Saving…", tone: "info" });
    const err = await send(base, patch);
    if (err) setMsg({ text: err, tone: "error" });
    else setPending(patch);
  };
  return (
    <Box title="Emergency script">
      <p className="text-xs text-neutral-500">
        What a buddy or volunteer does, in your words and your order. They see it only while their claim is live, and
        they follow it exactly: it is never medical advice from anyone else.
      </p>
      <ol className="flex flex-col gap-2">
        {steps.map((s, i) => (
          <li key={i} className="flex gap-2 items-start">
            <span className="pt-2 text-neutral-500 tabular-nums w-5 text-right">{i + 1}.</span>
            <textarea
              aria-label={`Step ${i + 1}`}
              rows={2}
              className="flex-1 bg-neutral-900 border border-neutral-700 rounded-lg px-3 py-2 text-white"
              value={s}
              onChange={(e) => edit(steps.map((x, j) => (j === i ? e.target.value : x)))}
            />
            <div className="flex flex-col">
              <button type="button" aria-label="Move up" disabled={i === 0} className="px-2 text-neutral-400 disabled:opacity-30"
                onClick={() => edit(steps.map((x, j) => (j === i - 1 ? steps[i] : j === i ? steps[i - 1] : x)))}>
                ↑
              </button>
              <button type="button" aria-label="Remove" className="px-2 text-red-300"
                onClick={() => edit(steps.filter((_, j) => j !== i))}>
                ×
              </button>
            </div>
          </li>
        ))}
      </ol>
      <div className="flex gap-2">
        <button type="button" className="rounded-lg px-3 py-2 bg-neutral-900 text-neutral-200" onClick={() => edit([...steps, ""])}>
          Add a step
        </button>
        <button type="button" disabled={!dirty || !!pending}
          className="flex-1 rounded-lg px-3 py-2 bg-sky-400 text-black font-semibold disabled:opacity-40" onClick={save}>
          Save script
        </button>
      </div>
      {saved.length === 0 && !dirty && <p className="text-sm text-neutral-400">No script yet.</p>}
      {msg && <p role="status" className={`text-sm ${tone(msg.tone)}`}>{msg.text}</p>}
    </Box>
  );
}

function BuddyCard({ state, base }: { state: ReturnType<typeof readBuddyState>; base: string }) {
  const [ask, setAsk] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const { link, morningLine } = state;
  const revoke = async () => {
    if (!link?.id || busy) return;
    setBusy(true);
    try {
      const r = await revokePairing(base, link.id);
      setMsg(r.ok ? `Buddy pairing with ${link.name} ended. Keys are deleted on both sides.` : r.reason);
    } catch (e) {
      if (!(e instanceof PinRejected)) setMsg("Could not reach your Irin.");
    } finally {
      setBusy(false);
      setAsk(false);
    }
  };
  return (
    <Box title="My buddy">
      {link ? (
        <>
          <div className="flex items-center gap-2">
            <span className="text-lg font-semibold">{link.name}</span>
            {link.kind && (
              <span className="text-xs rounded-full border border-sky-600 text-sky-200 px-2 py-0.5">
                {link.kind === "twin" ? "twin: same sleep hours" : "mirror: awake while you sleep"}
              </span>
            )}
            {link.id && !ask && (
              <button type="button" className="ml-auto text-sm text-red-300 underline" onClick={() => setAsk(true)}>
                Revoke
              </button>
            )}
          </div>
          {ask && (
            <div className="rounded-lg bg-neutral-900 p-3 flex flex-col gap-2">
              <p className="text-sm">End the buddy pairing with {link.name}? They stop being alerted now. To pair again you start over.</p>
              <div className="flex gap-2">
                <button type="button" className="flex-1 rounded-lg bg-neutral-800 py-2" onClick={() => setAsk(false)}>
                  Keep
                </button>
                <button type="button" disabled={busy} className="flex-1 rounded-lg bg-red-700 py-2 font-semibold disabled:opacity-40" onClick={revoke}>
                  End pairing
                </button>
              </div>
            </div>
          )}
        </>
      ) : (
        <p className="text-sm text-neutral-400">No buddy paired yet.</p>
      )}
      {morningLine && <p className="text-sm text-neutral-200 border-l-2 border-sky-500 pl-3">{morningLine}</p>}
      {msg && <p role="status" className="text-sm text-amber-300">{msg}</p>}
    </Box>
  );
}

export default function BuddyTab() {
  const { target, socket } = useDevice();
  const snap = socket.snapshot;
  if (target.status !== "ready") return <p className="text-neutral-400">Pair your Irin on the Device tab first.</p>;
  if (!snap) return <p className="text-neutral-400">Waiting for your Irin…</p>;
  const base = target.url;
  const state = readBuddyState(snap);
  return (
    <section className="flex flex-col gap-4">
      <h2 className="text-xl font-semibold">Irin Buddy</h2>
      <TreatingButton snap={snap} baseUrl={base} />
      <BuddyCard state={state} base={base} />
      {snap.settings && <OptInToggles current={snap.settings} base={base} />}
      {snap.settings && <ScriptEditor current={snap.settings} base={base} />}
    </section>
  );
}
