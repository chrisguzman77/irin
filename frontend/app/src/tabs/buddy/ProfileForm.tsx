import { useEffect, useState } from "react";
import { PinRejected } from "../../lib/api";
import { getProfile, saveProfile, type BuddyProfile, type ProfileReply, type Slot } from "../../lib/buddy";
import type { Settings } from "../../lib/contracts";

// B3+: the directory profile, POSTed to the Pi (which verifies the CGM feed
// itself and forwards to the relay's /v0/users). Only these fields exist:
// no glucose, location, phone, or email (invariant 15). The opt-ins are the
// four toggles below this box (settings.night_buddy), sent as they stand.
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const OFF = { have_buddy: false, be_watcher: false, hub_watchable: false, hub_volunteer: false };
const list = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);
const input = "bg-neutral-900 border border-neutral-700 rounded-lg px-3 py-2 text-white";

export default function ProfileForm({ base, settings, demo }: { base: string; settings: Settings | undefined; demo: boolean }) {
  const [reply, setReply] = useState<ProfileReply | null>(null);
  const [username, setUsername] = useState("");
  const [firstName, setFirstName] = useState("");
  const [languages, setLanguages] = useState("English");
  const [timezones, setTimezones] = useState(() => Intl.DateTimeFormat().resolvedOptions().timeZone);
  const [slots, setSlots] = useState<Slot[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null);

  const fill = (r: ProfileReply) => {
    setReply(r);
    const p = r.profile;
    if (!p) return;
    setUsername(p.username ?? "");
    setFirstName(p.first_name ?? "");
    setLanguages((p.languages ?? []).join(", "));
    setTimezones((p.timezones ?? []).join(", "));
    setSlots(p.availability ?? []);
  };

  useEffect(() => {
    getProfile(base)
      .then((r) => (r.ok ? fill(r.value) : setMsg({ text: r.reason, ok: false })))
      .catch((e) => !(e instanceof PinRejected) && setMsg({ text: "Could not reach your Irin.", ok: false }));
  }, [base]);

  const save = async () => {
    const body: BuddyProfile = {
      username: username.trim(),
      first_name: firstName.trim(),
      languages: list(languages),
      timezones: list(timezones),
      availability: slots,
      optins: { ...OFF, ...(settings?.night_buddy ?? {}) },
    };
    setBusy(true);
    setMsg(null);
    try {
      const r = await saveProfile(base, body);
      if (r.ok) {
        fill(r.value);
        setMsg({ text: "Profile saved.", ok: true });
      } else setMsg({ text: r.reason, ok: false });
    } catch (e) {
      if (!(e instanceof PinRejected)) setMsg({ text: "Could not reach your Irin. Nothing was saved.", ok: false });
    } finally {
      setBusy(false);
    }
  };

  const setSlot = (i: number, patch: Partial<Slot>) => setSlots(slots.map((s, j) => (j === i ? { ...s, ...patch } : s)));

  return (
    <section className="rounded-xl border border-neutral-800 px-4 py-3 flex flex-col gap-2">
      <h3 className="text-xs uppercase tracking-wider text-neutral-400 flex items-center gap-2">
        Buddy profile
        {demo && <span className="bg-amber-400 text-black text-xs font-bold px-2 py-0.5 rounded">DEMO</span>}
      </h3>
      {reply?.profile &&
        (reply.cgm_verified ? (
          <p className="text-sm text-emerald-400">CGM verified: your Irin checked your feed. You can be matched.</p>
        ) : (
          <p className="text-sm text-amber-300">
            Not CGM verified{reply.cgm_reason ? `: ${reply.cgm_reason}` : ": your Irin has not confirmed a live CGM feed"}. Unverified
            profiles are never listed or matched.
          </p>
        ))}
      <label className="flex flex-col gap-1 text-sm text-neutral-300">
        Username
        <input className={input} value={username} autoComplete="off" onChange={(e) => setUsername(e.target.value)} />
      </label>
      <label className="flex flex-col gap-1 text-sm text-neutral-300">
        First name
        <input className={input} value={firstName} autoComplete="off" onChange={(e) => setFirstName(e.target.value)} />
      </label>
      <label className="flex flex-col gap-1 text-sm text-neutral-300">
        Languages (comma separated)
        <input className={input} value={languages} onChange={(e) => setLanguages(e.target.value)} />
      </label>
      <label className="flex flex-col gap-1 text-sm text-neutral-300">
        Time zones (comma separated, e.g. America/New_York)
        <input className={input} value={timezones} onChange={(e) => setTimezones(e.target.value)} />
      </label>
      <div className="flex flex-col gap-1 text-sm text-neutral-300">
        Hours I'm awake to watch (my local time)
        {slots.map((s, i) => (
          <div key={i} className="flex flex-wrap gap-2 items-center">
            <select aria-label="Day" className={input} value={s.weekday} onChange={(e) => setSlot(i, { weekday: Number(e.target.value) })}>
              {DAYS.map((d, n) => (
                <option key={d} value={n}>{d}</option>
              ))}
            </select>
            <input aria-label="From" type="time" className={input} value={s.start} onChange={(e) => setSlot(i, { start: e.target.value })} />
            <span>to</span>
            <input aria-label="Until" type="time" className={input} value={s.end} onChange={(e) => setSlot(i, { end: e.target.value })} />
            <button type="button" aria-label="Remove" className="px-2 text-red-300" onClick={() => setSlots(slots.filter((_, j) => j !== i))}>
              ×
            </button>
          </div>
        ))}
        <button type="button" className="self-start rounded-lg px-3 py-2 bg-neutral-900 text-neutral-200"
          onClick={() => setSlots([...slots, { weekday: 0, start: "22:00", end: "06:00" }])}>
          Add hours
        </button>
      </div>
      <p className="text-xs text-neutral-500">
        No glucose values, places, phone numbers or emails are asked for or shared. A buddy sees your first name only.
      </p>
      <button type="button" disabled={busy || !username.trim() || !firstName.trim() || list(timezones).length === 0}
        className="rounded-lg px-3 py-2 bg-sky-400 text-black font-semibold disabled:opacity-40" onClick={save}>
        Save profile
      </button>
      {msg && <p role="status" className={`text-sm ${msg.ok ? "text-emerald-400" : "text-red-400"}`}>{msg.text}</p>}
    </section>
  );
}
