import { useState } from "react";
import { PinRejected } from "../../lib/api";
import PiAudio from "../../components/PiAudio";
import type { StateSnapshot } from "../../lib/contracts";
import { approveStory, pauseRecipient, skipStory, storyAudioPath, type FamilyStory } from "../../lib/family";

// Family Story F2: the morning chip ("Sent to Mom") with one-tap Pause per
// recipient, and the approval screen for a pending story (Send / Skip, PIN).
// Rendered from the snapshot's family_story_status plus family_story_pending /
// family_story_sent; a story shows as sent ONLY when the Pi says status
// "sent". Demo stories are badged DEMO and say they were not sent.

const dateLabel = (d: string) =>
  new Date(`${d}T12:00:00`).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
const hhmm = (iso: string) =>
  new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });

const btn = "rounded-lg px-3 py-2 text-sm font-semibold disabled:opacity-40";

export default function FamilyStories({ snap, baseUrl }: { snap: StateSnapshot; baseUrl: string }) {
  // Approve and skip answer with the updated story, but skip is not broadcast:
  // keep the Pi's answers here, laid over the snapshot's list.
  const [answered, setAnswered] = useState<Record<string, FamilyStory>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ text: string; error: boolean } | null>(null);

  const stories = ((snap.family_story_status ?? []) as unknown as FamilyStory[]).map((s) => answered[s.story_id] ?? s);
  if (stories.length === 0) return null;
  const recipients = snap.settings?.family_recipients ?? [];
  const who = (id: string) => recipients.find((r) => r.recipient_id === id);

  const run = async (key: string, call: () => Promise<{ ok: true; value: unknown } | { ok: false; reason: string }>, done: string) => {
    if (busy) return;
    setBusy(key);
    setMsg(null);
    try {
      const res = await call();
      if (!res.ok) setMsg({ text: res.reason, error: true });
      else {
        const v = res.value as Partial<FamilyStory>;
        if (typeof v.story_id === "string") setAnswered((a) => ({ ...a, [v.story_id as string]: v as FamilyStory }));
        setMsg({ text: done, error: false });
      }
    } catch (e) {
      if (!(e instanceof PinRejected)) setMsg({ text: "Could not reach your Irin.", error: true });
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="mt-4 rounded-xl border border-neutral-800 px-4 py-3 flex flex-col gap-3">
      <h3 className="text-sm uppercase tracking-wider text-neutral-400">
        Family · night of {dateLabel(stories[0].night_date)}
      </h3>
      <ul className="flex flex-col gap-3">
        {stories.map((s) => {
          const r = who(s.recipient_id);
          const name = r?.name ?? "a recipient";
          const audio = storyAudioPath(s);
          const status = s.status ?? "pending_approval";
          return (
            <li key={s.story_id} className="flex flex-col gap-2">
              <div className="flex items-center gap-2 flex-wrap">
                {status === "sent" && (
                  <span className="bg-emerald-500 text-black text-sm font-bold px-2.5 py-1 rounded-full">
                    Sent to {name}
                    {s.sent_at ? ` · ${hhmm(s.sent_at)}` : ""}
                  </span>
                )}
                {status === "demo" && (
                  <>
                    <span className="bg-amber-400 text-black text-xs font-bold px-2 py-0.5 rounded">DEMO</span>
                    <span className="text-sm text-neutral-300">For {name}: not sent (demo)</span>
                  </>
                )}
                {status === "pending_approval" && (
                  <span className="text-sm font-semibold text-amber-300">For {name}: waiting for your approval</span>
                )}
                {status === "skipped" && <span className="text-sm text-neutral-400">For {name}: skipped, nothing sent</span>}
                {status === "failed" && <span className="text-sm text-red-400">For {name}: could not be sent</span>}
                {s.is_demo && status !== "demo" && (
                  <span className="bg-amber-400 text-black text-xs font-bold px-2 py-0.5 rounded">DEMO</span>
                )}
                {r?.state === "active" && (
                  <button
                    type="button"
                    disabled={!!busy}
                    className={`${btn} ml-auto bg-neutral-800 text-neutral-200`}
                    onClick={() => run(`pause-${r.recipient_id}`, () => pauseRecipient(baseUrl, r.recipient_id), `${name} paused.`)}
                  >
                    Pause {name}
                  </button>
                )}
                {r?.state === "paused" && <span className="ml-auto text-xs text-neutral-500">{name} is paused</span>}
                {r?.state === "revoked" && <span className="ml-auto text-xs text-neutral-500">{name} is revoked</span>}
              </div>

              {status === "pending_approval" ? (
                <div className="rounded-lg bg-neutral-900 p-3 flex flex-col gap-3">
                  <p className="whitespace-pre-line text-neutral-100 leading-relaxed">{s.text}</p>
                  {audio && <PiAudio base={baseUrl} path={audio} className="w-full" />}
                  <p className="text-xs text-neutral-500">
                    {s.level === "story_only" ? "Story only: no glucose numbers." : "Story + view: includes the night's numbers."}
                  </p>
                  <div className="flex gap-2">
                    <button
                      type="button"
                      disabled={!!busy}
                      className={`${btn} border border-neutral-700 text-neutral-300`}
                      onClick={() => run(`skip-${s.story_id}`, () => skipStory(baseUrl, s.story_id), "Skipped. Nothing was sent.")}
                    >
                      Skip
                    </button>
                    <button
                      type="button"
                      disabled={!!busy}
                      className={`${btn} flex-1 bg-amber-400 text-black`}
                      onClick={() =>
                        run(`send-${s.story_id}`, () => approveStory(baseUrl, s.story_id), s.is_demo ? "Approved (demo: not emailed)." : `Approved for ${name}.`)
                      }
                    >
                      Send to {name}
                    </button>
                  </div>
                </div>
              ) : (
                (status === "sent" || status === "demo") && (
                  <details className="text-sm text-neutral-400">
                    <summary className="cursor-pointer">What {name} {status === "sent" ? "received" : "would receive"}</summary>
                    <p className="whitespace-pre-line text-neutral-200 mt-2">{s.text}</p>
                    {audio && <PiAudio base={baseUrl} path={audio} className="w-full mt-2" />}
                  </details>
                )
              )}
            </li>
          );
        })}
      </ul>
      {msg && (
        <p role="status" className={`text-sm ${msg.error ? "text-red-400" : "text-emerald-400"}`}>
          {msg.text}
        </p>
      )}
    </section>
  );
}
