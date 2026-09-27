import { useEffect, useState } from "react";
import { PinRejected } from "../../lib/api";
import { getProfile, saveBuddySettings, saveProfile, type MatchState, type Slot } from "../../lib/buddy";
import type { Settings } from "../../lib/contracts";
import FindBuddy from "./FindBuddy";
import GlobePicker from "./GlobePicker";
import SlotEditor from "./SlotEditor";

// Buddy onboarding v2 (relay/README.md, PINNED): the guided path for someone
// with no buddy yet. timezones = [home, preferred buddy zone]; languages are
// deduped by casefold and sent in Title Case; the opt-ins start off (invariant
// 16). The draft lives in sessionStorage (never the URL) so a re-render or a
// reload keeps it. Nothing here asks for a glucose value, place, phone, or
// email (invariant 15).
type OptIns = NonNullable<Settings["night_buddy"]>;
const OPT_INS: [keyof OptIns, string, string][] = [
  ["have_buddy", "Have a buddy", "One paired T1D adult is the last human rung of your alarm ladder."],
  ["be_watcher", "Be a watcher", "Your phone can be alerted when your buddy's low goes unanswered."],
  ["hub_watchable", "Hub-watchable", "If nobody answers, a pseudonymous listing asks verified volunteers for help. No glucose value, place, or number is ever shown."],
  ["hub_volunteer", "Hub volunteer", "You may see listings from people you have never met and claim one."],
];
const OFF: OptIns = { have_buddy: false, be_watcher: false, hub_watchable: false, hub_volunteer: false };

const FALLBACK_ZONES = ["America/New_York", "America/Chicago", "America/Denver", "America/Phoenix", "America/Los_Angeles",
  "America/Anchorage", "Pacific/Honolulu", "America/Halifax", "America/Sao_Paulo", "America/Mexico_City", "Europe/London",
  "Europe/Paris", "Europe/Berlin", "Europe/Madrid", "Europe/Athens", "Europe/Moscow", "Africa/Lagos", "Africa/Johannesburg",
  "Asia/Dubai", "Asia/Kolkata", "Asia/Bangkok", "Asia/Shanghai", "Asia/Singapore", "Asia/Tokyo", "Asia/Seoul",
  "Australia/Perth", "Australia/Sydney", "Pacific/Auckland", "UTC"];
function zoneList(): string[] {
  const intl = Intl as unknown as { supportedValuesOf?: (k: string) => string[] };
  let all: string[] = [];
  try {
    all = intl.supportedValuesOf?.("timeZone") ?? [];
  } catch {
    /* older engine */
  }
  if (all.length === 0) all = FALLBACK_ZONES;
  return ["America/New_York", ...all.filter((z) => z !== "America/New_York")];
}
const ZONES = zoneList();
const browserZone = () => {
  const z = Intl.DateTimeFormat().resolvedOptions().timeZone;
  return ZONES.includes(z) ? z : "America/New_York";
};

const titleCase = (s: string) => s.trim().toLocaleLowerCase().replace(/(^|[\s-])(\p{L})/gu, (_, a, b) => a + b.toLocaleUpperCase());
/** casefold dedupe, Title Case display (the relay compares by casefold too) */
function addLanguages(list: string[], raw: string): string[] {
  const out = [...list];
  for (const part of raw.split(",")) {
    const t = titleCase(part);
    if (t && !out.some((x) => x.toLocaleLowerCase() === t.toLocaleLowerCase())) out.push(t);
  }
  return out;
}

interface Draft {
  step: number; // 0 = the "Find me a buddy" button, 1-6 = the wizard
  username: string;
  first_name: string;
  languages: string[];
  home: string;
  buddyZone: string | null;
  slots: Slot[];
  optins: OptIns;
  script: string;
  prefilled: boolean;
}
const KEY = "irin.buddyWizard";
const fresh = (): Draft => ({
  step: 0, username: "", first_name: "", languages: [], home: browserZone(), buddyZone: null, slots: [],
  optins: { ...OFF }, script: "", prefilled: false,
});
function load(): Draft {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (raw) return { ...fresh(), ...(JSON.parse(raw) as Partial<Draft>) };
  } catch {
    /* storage blocked */
  }
  return fresh();
}

const input = "bg-neutral-900 border border-neutral-700 rounded-lg px-3 py-2 text-white";
const primary = "flex-1 rounded-lg px-3 py-3 bg-sky-400 text-black font-semibold disabled:opacity-40";
const TITLES = ["", "About you", "Where should your buddy be?", "When can you watch?", "Opt-ins", "Your emergency script", "Finding your buddy"];
const badge = <span className="bg-amber-400 text-black text-xs font-bold px-2 py-0.5 rounded">DEMO</span>;

export default function BuddyWizard({ base, demo, settings, matches }: {
  base: string; demo: boolean; settings: Settings | undefined; matches: MatchState[];
}) {
  const [d, setD] = useState<Draft>(load);
  const [langInput, setLangInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const set = (patch: Partial<Draft>) => setD((x) => ({ ...x, ...patch }));
  const go = (step: number) => {
    setErr("");
    set({ step });
  };

  useEffect(() => {
    try {
      sessionStorage.setItem(KEY, JSON.stringify(d));
    } catch {
      /* storage blocked: component state still holds the draft */
    }
  }, [d]);

  // once per draft: start from what this Irin already holds (a saved profile, the script)
  useEffect(() => {
    if (d.step === 0 || d.prefilled) return;
    set({ prefilled: true });
    const steps = settings?.emergency_script?.steps ?? [];
    if (steps.length) setD((x) => (x.script ? x : { ...x, script: steps.join("\n") }));
    getProfile(base)
      .then((r) => {
        const p = r.ok ? r.value.profile : null;
        if (!p) return;
        setD((x) => ({
          ...x,
          username: x.username || p.username || "",
          first_name: x.first_name || p.first_name || "",
          languages: x.languages.length ? x.languages : addLanguages([], (p.languages ?? []).join(",")),
          home: p.timezones?.[0] && ZONES.includes(p.timezones[0]) ? p.timezones[0] : x.home,
          buddyZone: x.buddyZone ?? p.timezones?.[1] ?? null,
          slots: x.slots.length ? x.slots : (p.availability ?? []),
        }));
      })
      .catch(() => {
        /* no profile yet, or unreachable: the user types it */
      });
  }, [d.step, d.prefilled, base, settings]);

  const saveAll = async () => {
    setBusy(true);
    setErr("");
    try {
      const steps = d.script.split("\n").map((s) => s.trim()).filter(Boolean);
      const s = await saveBuddySettings(base, { night_buddy: d.optins, emergency_script: { steps } });
      if (!s.ok) return setErr(s.reason);
      const p = await saveProfile(base, {
        username: d.username.trim(),
        first_name: d.first_name.trim(),
        languages: d.languages,
        timezones: d.buddyZone ? [d.home, d.buddyZone] : [d.home],
        availability: d.slots,
        optins: d.optins,
      });
      if (!p.ok) return setErr(p.reason);
      go(6);
    } catch (e) {
      if (!(e instanceof PinRejected)) setErr("Could not reach your Irin. Nothing more was saved.");
    } finally {
      setBusy(false);
    }
  };

  if (d.step === 0)
    return (
      <div className="flex flex-col items-center gap-3 py-10">
        {demo && badge}
        <button type="button" className="w-full rounded-2xl py-6 bg-sky-400 text-black text-2xl font-bold" onClick={() => go(1)}>
          Find me a buddy
        </button>
      </div>
    );

  const step1ok = d.username.trim() && d.first_name.trim() && d.languages.length > 0 && d.home;
  const anyOptIn = Object.values(d.optins).some(Boolean);
  const nav = (next: React.ReactNode) => (
    <div className="flex gap-2 pt-2">
      <button type="button" className="rounded-lg px-4 py-3 bg-neutral-800 text-neutral-200" onClick={() => go(d.step - 1)}>
        Back
      </button>
      {next}
    </div>
  );
  const next = (ok: unknown) => (
    <button type="button" disabled={!ok} className={primary} onClick={() => go(d.step + 1)}>
      Next
    </button>
  );

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2" aria-label={`Step ${d.step} of 6`}>
        {[1, 2, 3, 4, 5, 6].map((n) => (
          <span key={n} className={`size-2.5 rounded-full ${n === d.step ? "bg-sky-400" : n < d.step ? "bg-sky-800" : "bg-neutral-700"}`} />
        ))}
        {demo && <span className="ml-auto">{badge}</span>}
      </div>
      <h3 className="text-lg font-semibold">{TITLES[d.step]}</h3>

      {d.step === 1 && (
        <>
          <label className="flex flex-col gap-1 text-sm text-neutral-300">
            Username
            <input className={input} value={d.username} autoComplete="off" onChange={(e) => set({ username: e.target.value })} />
          </label>
          <label className="flex flex-col gap-1 text-sm text-neutral-300">
            First name
            <input className={input} value={d.first_name} autoComplete="off" onChange={(e) => set({ first_name: e.target.value })} />
          </label>
          <div className="flex flex-col gap-1 text-sm text-neutral-300">
            Languages
            {d.languages.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {d.languages.map((l) => (
                  <span key={l} className="flex items-center gap-1 rounded-full bg-neutral-800 px-3 py-1">
                    {l}
                    <button type="button" aria-label={`Remove ${l}`} className="text-neutral-400"
                      onClick={() => set({ languages: d.languages.filter((x) => x !== l) })}>
                      ×
                    </button>
                  </span>
                ))}
              </div>
            )}
            <input className={input} value={langInput} placeholder="Type a language, then comma or Enter"
              onChange={(e) => {
                const v = e.target.value;
                if (v.includes(",")) {
                  set({ languages: addLanguages(d.languages, v) });
                  setLangInput("");
                } else setLangInput(v);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  set({ languages: addLanguages(d.languages, langInput) });
                  setLangInput("");
                }
              }}
              onBlur={() => {
                if (langInput.trim()) {
                  set({ languages: addLanguages(d.languages, langInput) });
                  setLangInput("");
                }
              }}
            />
          </div>
          <label className="flex flex-col gap-1 text-sm text-neutral-300">
            Your time zone
            <select className={input} value={d.home} onChange={(e) => set({ home: e.target.value })}>
              {ZONES.map((z) => (
                <option key={z} value={z}>{z.replaceAll("_", " ")}</option>
              ))}
            </select>
          </label>
          <p className="text-xs text-neutral-500">
            No glucose values, places, phone numbers or emails are asked for or shared. A buddy sees your first name only.
          </p>
          {nav(next(step1ok))}
        </>
      )}

      {d.step === 2 && (
        <>
          <GlobePicker initialZone={d.home} value={d.buddyZone} onChange={(z) => set({ buddyZone: z })} />
          {d.buddyZone && <p className="text-sm text-neutral-300">Buddy zone: {d.buddyZone.replaceAll("_", " ")}</p>}
          {nav(next(d.buddyZone))}
        </>
      )}

      {d.step === 3 && (
        <>
          <SlotEditor slots={d.slots} onChange={(slots) => set({ slots })} />
          {nav(next(true))}
        </>
      )}

      {d.step === 4 && (
        <>
          {OPT_INS.map(([k, label, about]) => (
            <label key={k} className="flex items-start justify-between gap-3 py-1">
              <span className="flex flex-col">
                <span className="text-sm text-neutral-200">{label}</span>
                <span className="text-xs text-neutral-500">{about}</span>
              </span>
              <input type="checkbox" className="size-6 shrink-0 accent-sky-400" checked={d.optins[k]}
                onChange={() => set({ optins: { ...d.optins, [k]: !d.optins[k] } })} />
            </label>
          ))}
          {nav(next(anyOptIn))}
          {!d.optins.have_buddy && (
            <p className="text-sm text-amber-300">
              Matching needs "Have a buddy" on. You can still continue and save; you will not be matched until you turn it on.
            </p>
          )}
        </>
      )}

      {d.step === 5 && (
        <>
          <p className="text-xs text-neutral-500">
            What a buddy or volunteer does, in your words and your order, one step per line. They see it only while their
            claim is live, and they follow it exactly: it is never medical advice from anyone else.
          </p>
          <textarea aria-label="Emergency script, one step per line" rows={6} className={input} value={d.script}
            onChange={(e) => set({ script: e.target.value })} />
          {nav(
            <button type="button" disabled={busy} className={primary} onClick={saveAll}>
              {busy ? "Saving…" : "Save and find my buddy"}
            </button>,
          )}
        </>
      )}

      {d.step === 6 && (
        <>
          <FindBuddy base={base} matches={matches} demo={demo} autoFind />
          {nav(null)}
        </>
      )}

      {err && <p role="status" className="text-sm text-red-400">{err}</p>}
    </div>
  );
}
