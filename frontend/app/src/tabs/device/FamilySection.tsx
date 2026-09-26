import { useEffect, useState } from "react";
import { PinRejected } from "../../lib/api";
import type { Settings } from "../../lib/contracts";
import {
  addRecipient, editRecipient, pauseRecipient, resumeRecipient, revokeRecipient, type Recipient,
} from "../../lib/family";
import { useDevice } from "../../lib/device";
import { revokeViewLink } from "../../lib/familyView";
import { sameValue } from "../../lib/settings";
import FamilyViewLink from "./FamilyViewLink";

// Family Story F1 (docs/FAMILY_STORY.md): who receives the morning story,
// how much they see, and whether each story waits for approval. Read from
// Settings.family_recipients; changed only through the Pi's PIN-gated
// recipient routes (add, edit, pause, resume, revoke). Each action counts
// only once the Pi's settings_change carries the recipient as the route
// returned it. Revoked recipients stay on the list, marked, with no actions.
type Level = NonNullable<Recipient["level"]>;
type SendMode = NonNullable<Recipient["send_mode"]>;

const ECHO_WAIT_MS = 5000;
const LEVELS: [Level, string, string][] = [
  ["story_only", "Story only", "A plain-language story of the night; never a glucose number."],
  ["story_and_view", "Story + view", "The story plus the night's numbers."],
];
// [value, short label for the per-person row, full label for the add form]
const MODES: [SendMode, string, string][] = [
  ["approve_each", "Approve each", "Approve each morning (default)"],
  ["automatic", "Automatic", "Automatic"],
];

const input = "bg-neutral-900 border border-neutral-700 rounded-lg px-3 py-2 text-white w-full";
const btn = "rounded-lg px-3 py-2 text-sm font-semibold disabled:opacity-40";
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

export default function FamilySection({ current, baseUrl }: { current: Settings | undefined; baseUrl: string }) {
  const list: Recipient[] = current?.family_recipients ?? [];
  const [pending, setPending] = useState<Recipient | null>(null);
  const [msg, setMsg] = useState<{ text: string; tone: "info" | "ok" | "error" } | null>(null);
  const [confirmRevoke, setConfirmRevoke] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [level, setLevel] = useState<Level>("story_only");
  const [mode, setMode] = useState<SendMode>("approve_each");
  const demo = useDevice().socket.snapshot?.mode === "replay";

  /** A recipient who is paused, revoked, or moved to Story only loses their live view link too. */
  const linkOff = async (r: Recipient) => {
    const res = await revokeViewLink(r.recipient_id);
    if (!res.ok) setMsg({ text: `${r.name}: ${res.reason}`, tone: "error" });
  };

  useEffect(() => {
    if (pending && current && (current.family_recipients ?? []).some((r) => sameValue(r, pending))) {
      setPending(null);
      setMsg({ text: "Saved on your Irin.", tone: "ok" });
    }
  }, [current, pending]);

  useEffect(() => {
    if (!pending) return;
    const id = window.setTimeout(
      () => setMsg({ text: "Your Irin accepted it but has not confirmed yet. Check the connection.", tone: "error" }),
      ECHO_WAIT_MS,
    );
    return () => window.clearTimeout(id);
  }, [pending]);

  const busy = !!pending;

  /** One recipient route; true when the Pi accepted it. */
  const act = async (
    call: () => Promise<{ ok: true; value: Recipient } | { ok: false; reason: string }>,
    sending: string,
  ): Promise<boolean> => {
    if (busy) return false;
    setMsg({ text: sending, tone: "info" });
    try {
      const res = await call();
      if (!res.ok) {
        setMsg({ text: res.reason, tone: "error" });
        return false;
      }
      setPending(res.value);
      return true;
    } catch (e) {
      if (!(e instanceof PinRejected)) setMsg({ text: "Could not reach your Irin. Nothing was saved.", tone: "error" });
      return false;
    }
  };

  const nameOk = name.trim().length > 0 && name.trim().length <= 60;
  const emailOk = EMAIL.test(email.trim());
  const duplicate = list.some((r) => r.state !== "revoked" && r.email.toLowerCase() === email.trim().toLowerCase());

  const add = async () => {
    if (!nameOk || !emailOk || duplicate) return;
    const who = name.trim();
    const ok = await act(
      () => addRecipient(baseUrl, { name: who, email: email.trim(), level, send_mode: mode }),
      `Adding ${who}…`,
    );
    if (ok) {
      setAdding(false);
      setName("");
      setEmail("");
      setLevel("story_only");
      setMode("approve_each");
    }
  };

  if (!current) return null;

  return (
    <fieldset className="border border-neutral-800 rounded-xl px-4 py-3 mb-4 flex flex-col gap-3">
      <legend className="px-1 text-neutral-400 text-xs uppercase tracking-wider">Family</legend>
      <p className="text-sm text-neutral-400">
        People who get a short story of your night by email each morning. The first story to each person always
        waits for your approval; after that it follows the sending choice.
      </p>

      {list.length === 0 && <p className="text-sm text-neutral-500">No one yet.</p>}

      <ul className="flex flex-col gap-2">
        {list.map((r) => {
          const revoked = r.state === "revoked";
          const paused = r.state === "paused";
          return (
            <li key={r.recipient_id} className={`rounded-lg bg-neutral-900 px-3 py-3 flex flex-col gap-2 ${revoked ? "opacity-50" : ""}`}>
              <div className="flex items-center gap-2">
                <div className="flex-1 min-w-0">
                  <div className="font-semibold truncate">{r.name}</div>
                  <div className="text-xs text-neutral-400 truncate">{r.email}</div>
                </div>
                {revoked && <span className="text-xs border border-neutral-600 text-neutral-400 px-2 py-0.5 rounded">revoked</span>}
                {paused && <span className="text-xs bg-neutral-700 text-neutral-200 px-2 py-0.5 rounded">paused</span>}
                {!revoked && !r.first_story_approved && (
                  <span className="text-xs text-amber-300">first story needs approval</span>
                )}
              </div>
              {!revoked && (
                <>
                  <div className="grid grid-cols-2 gap-2">
                    <select
                      aria-label={`What ${r.name} receives`}
                      className={input}
                      disabled={busy}
                      value={r.level ?? "story_only"}
                      onChange={async (e) => {
                        const next = e.target.value as Level;
                        if ((await act(() => editRecipient(baseUrl, r.recipient_id, { level: next }), "Saving…")) && next === "story_only") await linkOff(r);
                      }}
                    >
                      {LEVELS.map(([v, label]) => (
                        <option key={v} value={v}>{label}</option>
                      ))}
                    </select>
                    <select
                      aria-label={`How ${r.name}'s story is sent`}
                      className={input}
                      disabled={busy}
                      value={r.send_mode ?? "approve_each"}
                      onChange={(e) =>
                        act(() => editRecipient(baseUrl, r.recipient_id, { send_mode: e.target.value as SendMode }), "Saving…")
                      }
                    >
                      {MODES.map(([v, short]) => (
                        <option key={v} value={v}>{short}</option>
                      ))}
                    </select>
                  </div>
                  {r.level === "story_and_view" && (
                    <FamilyViewLink recipientId={r.recipient_id} name={r.name} demo={demo} disabled={busy || paused} />
                  )}
                  {r.first_story_approved && (
                    <span className="text-xs text-neutral-500">
                      Changing what {r.name} receives means the next story waits for your approval again.
                    </span>
                  )}
                  {confirmRevoke === r.recipient_id ? (
                    <div className="flex flex-col gap-2 border border-red-800 rounded-lg p-2">
                      <span className="text-sm text-red-300">
                        Revoke {r.name}? They will never receive another story. To share again, add them anew.
                      </span>
                      <div className="flex gap-2">
                        <button type="button" className={`${btn} flex-1 border border-neutral-700 text-neutral-300`} onClick={() => setConfirmRevoke(null)}>
                          Keep
                        </button>
                        <button
                          type="button"
                          disabled={busy}
                          className={`${btn} flex-1 bg-red-600 text-white`}
                          onClick={async () => {
                            setConfirmRevoke(null);
                            if (await act(() => revokeRecipient(baseUrl, r.recipient_id), `Revoking ${r.name}…`)) await linkOff(r);
                          }}
                        >
                          Revoke
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div className="flex gap-2">
                      <button
                        type="button"
                        disabled={busy}
                        className={`${btn} flex-1 ${paused ? "bg-white text-black" : "bg-neutral-800 text-neutral-200"}`}
                        onClick={async () => {
                          if (paused) await act(() => resumeRecipient(baseUrl, r.recipient_id), `Resuming ${r.name}…`);
                          else if (await act(() => pauseRecipient(baseUrl, r.recipient_id), `Pausing ${r.name}…`)) await linkOff(r);
                        }}
                      >
                        {paused ? "Resume" : "Pause"}
                      </button>
                      <button
                        type="button"
                        disabled={busy}
                        className={`${btn} border border-red-800 text-red-300`}
                        onClick={() => setConfirmRevoke(r.recipient_id)}
                      >
                        Revoke
                      </button>
                    </div>
                  )}
                </>
              )}
            </li>
          );
        })}
      </ul>

      {adding ? (
        <div className="flex flex-col gap-2 rounded-lg border border-neutral-700 p-3">
          <input className={input} placeholder="Name" aria-label="Name" maxLength={60} value={name} onChange={(e) => setName(e.target.value)} />
          <input className={input} placeholder="Email" aria-label="Email" type="email" autoComplete="off" value={email} onChange={(e) => setEmail(e.target.value)} />
          <div className="flex flex-col gap-1">
            {LEVELS.map(([v, label, hint]) => (
              <label key={v} className="flex items-start gap-2 py-1">
                <input type="radio" name="family-level" className="mt-1 accent-amber-400" checked={level === v} onChange={() => setLevel(v)} />
                <span>
                  <span className="text-sm text-neutral-200">{label}</span>
                  <span className="block text-xs text-neutral-500">{hint}</span>
                </span>
              </label>
            ))}
          </div>
          <select aria-label="How the story is sent" className={input} value={mode} onChange={(e) => setMode(e.target.value as SendMode)}>
            {MODES.map(([v, , full]) => (
              <option key={v} value={v}>{full}</option>
            ))}
          </select>
          {email.trim() && !emailOk && <span className="text-xs text-red-400">Email looks wrong.</span>}
          {duplicate && <span className="text-xs text-red-400">That email is already on the list.</span>}
          <div className="flex gap-2">
            <button type="button" className={`${btn} border border-neutral-700 text-neutral-300`} onClick={() => setAdding(false)}>
              Cancel
            </button>
            <button type="button" disabled={busy || !nameOk || !emailOk || duplicate} className={`${btn} flex-1 bg-amber-400 text-black`} onClick={add}>
              Add
            </button>
          </div>
        </div>
      ) : (
        <button type="button" disabled={busy} className={`${btn} self-start bg-neutral-800 text-neutral-200`} onClick={() => setAdding(true)}>
          + Add someone
        </button>
      )}

      {msg && (
        <p role="status" className={`text-sm ${msg.tone === "ok" ? "text-green-400" : msg.tone === "error" ? "text-red-400" : "text-amber-300"}`}>
          {msg.text}
        </p>
      )}
    </fieldset>
  );
}
