import { useEffect, useState, type ReactNode } from "react";
import type { Settings } from "../../lib/contracts";
import { PinRejected } from "../../lib/api";
import { ALARM_SOUNDS, applied, changedFields, saveSettings, validate } from "../../lib/settings";
import FamilySection from "./FamilySection";

// Step 5: every device setting, drafted from the Pi's snapshot. Save sends
// only the changed fields; nothing is shown as saved until the Pi echoes it
// back (settings_change), and a reload then shows the Pi's values.
const ECHO_WAIT_MS = 5000;
const LED_STATES = [
  ["ambient", "Ambient"],
  ["warning", "Warning (predicted low)"],
  ["full", "Full alarm (actual low)"],
  ["stale", "Stale data"],
  ["high", "High"],
] as const;

const input = "bg-neutral-900 border border-neutral-700 rounded-lg px-3 py-2 text-white w-full";

function Row({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="flex flex-col gap-1 py-2">
      <span className="text-sm text-neutral-300">{label}</span>
      {children}
      {hint && <span className="text-xs text-neutral-500">{hint}</span>}
    </label>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <fieldset className="border border-neutral-800 rounded-xl px-4 py-2 mb-4">
      <legend className="px-1 text-neutral-400 text-xs uppercase tracking-wider">{title}</legend>
      {children}
    </fieldset>
  );
}

export default function SettingsForm({ current, baseUrl }: { current: Settings | undefined; baseUrl: string }) {
  const [draft, setDraft] = useState<Settings | null>(current ?? null);
  const [dirty, setDirty] = useState(false);
  const [msg, setMsg] = useState("");
  const [msgTone, setMsgTone] = useState<"info" | "ok" | "error">("info");
  const [sending, setSending] = useState(false);
  const [pending, setPending] = useState<Partial<Settings> | null>(null);
  // The Pi's settings when editing began: Save sends only what was edited
  // since then, so a change made meanwhile (the Family section, another
  // phone) is never sent back stale.
  const [base, setBase] = useState<Settings | null>(null);

  const say = (text: string, tone: "info" | "ok" | "error" = "info") => {
    setMsg(text);
    setMsgTone(tone);
  };

  // Follow the Pi while nothing is being edited.
  useEffect(() => {
    if (!dirty && current) setDraft(current);
  }, [current, dirty]);

  // A save counts only when the Pi's settings carry it.
  useEffect(() => {
    if (pending && current && applied(current, pending)) {
      setPending(null);
      setDirty(false);
      say("Saved. Your Irin is using the new settings.", "ok");
    }
  }, [current, pending]);

  useEffect(() => {
    if (!pending) return;
    const id = window.setTimeout(
      () => say("Your Irin accepted the change but has not confirmed it yet. Check the connection.", "error"),
      ECHO_WAIT_MS,
    );
    return () => window.clearTimeout(id);
  }, [pending]);

  if (!draft) return <p className="text-neutral-400">Waiting for your Irin's settings…</p>;

  const set = <K extends keyof Settings>(k: K, v: Settings[K]) => {
    if (!dirty) setBase(draft);
    setDraft({ ...draft, [k]: v });
    setDirty(true);
    setPending(null);
    setMsg("");
  };
  const num = (v: string) => (v === "" ? Number.NaN : Number(v));
  const errors = validate(draft);
  const leds = draft.led_colors ?? {};

  return (
    <>
    <form
      onSubmit={async (e) => {
        e.preventDefault();
        if (errors.length || !current || sending) return;
        const patch = changedFields(base ?? current, draft);
        if (Object.keys(patch).length === 0) {
          setDirty(false);
          say("Nothing changed.");
          return;
        }
        setSending(true);
        say("Saving…");
        try {
          const res = await saveSettings(baseUrl, patch);
          if (res.ok) {
            setPending(patch);
            say("Sent. Waiting for your Irin to confirm…");
          } else say(res.reason, "error");
        } catch (err) {
          if (err instanceof PinRejected) return; // the app returns to the code screen
          say("Could not reach your Irin. Nothing was saved.", "error");
        } finally {
          setSending(false);
        }
      }}
    >
      <Section title="Glucose alarms">
        <div className="grid grid-cols-2 gap-3">
          <Row label="Low (mg/dL)">
            <input className={input} type="number" inputMode="numeric" value={draft.low_threshold ?? ""}
              onChange={(e) => set("low_threshold", num(e.target.value))} />
          </Row>
          <Row label="High (mg/dL)">
            <input className={input} type="number" inputMode="numeric" value={draft.high_threshold ?? ""}
              onChange={(e) => set("high_threshold", num(e.target.value))} />
          </Row>
        </div>
        <p className="text-xs text-neutral-500 pb-1">Low alarms always sound, even in quiet hours.</p>
      </Section>

      <Section title="Predicted lows">
        <label className="flex items-center justify-between py-2">
          <span className="text-sm text-neutral-300">Warn before a low</span>
          <input type="checkbox" className="size-6 accent-amber-400" checked={!!draft.predictive_enabled}
            onChange={(e) => set("predictive_enabled", e.target.checked)} />
        </label>
        {draft.predictive_enabled && (
          <Row label="Lead time (minutes)">
            <input className={input} type="number" inputMode="numeric" value={draft.predictive_lead_min ?? ""}
              onChange={(e) => set("predictive_lead_min", num(e.target.value))} />
          </Row>
        )}
      </Section>

      <Section title="Highs">
        <Row label="High alert" hint="One-shot: a single chime and a persistent indicator until you come back down.">
          <select className={input} value={draft.high_alert_mode ?? "oneshot"}
            onChange={(e) => set("high_alert_mode", e.target.value as Settings["high_alert_mode"])}>
            <option value="oneshot">One-shot (default)</option>
            <option value="remind">Remind if still high</option>
          </select>
        </Row>
        {draft.high_alert_mode === "remind" && (
          <Row label="Remind after (hours)">
            <input className={input} type="number" inputMode="decimal" step="0.5" value={draft.high_remind_hours ?? ""}
              onChange={(e) => set("high_remind_hours", e.target.value === "" ? null : num(e.target.value))} />
          </Row>
        )}
      </Section>

      <Section title="Night">
        <div className="grid grid-cols-2 gap-3">
          <Row label="Night starts">
            <input className={input} type="time" value={draft.night_window_start ?? ""}
              onChange={(e) => set("night_window_start", e.target.value)} />
          </Row>
          <Row label="Night ends">
            <input className={input} type="time" value={draft.night_window_end ?? ""}
              onChange={(e) => set("night_window_end", e.target.value)} />
          </Row>
        </div>
        <Row label="Usual basal time" hint="Optional.">
          <input className={input} type="time" value={draft.basal_time ?? ""}
            onChange={(e) => set("basal_time", e.target.value || null)} />
        </Row>
      </Section>

      <Section title="Light">
        {LED_STATES.map(([k, label]) => (
          <label key={k} className="flex items-center justify-between py-2">
            <span className="text-sm text-neutral-300">{label}</span>
            <input type="color" className="h-9 w-16 bg-transparent" value={leds[k] ?? "#000000"}
              onChange={(e) => set("led_colors", { ...leds, [k]: e.target.value })} />
          </label>
        ))}
        <p className="text-xs text-neutral-500 pb-1">Warning and full alarm must stay easy to tell apart.</p>
      </Section>

      <Section title="Sound">
        <Row label="Alarm sound" hint="Preview arrives when your Irin serves its sounds.">
          <select className={input} value={draft.sound ?? "alarm_soft"} onChange={(e) => set("sound", e.target.value)}>
            {ALARM_SOUNDS.map((s) => (
              <option key={s} value={s}>
                {s.replace("_", " ")}
              </option>
            ))}
          </select>
        </Row>
        <Row label={`Volume ${Math.round((draft.volume ?? 0.8) * 100)}%`}>
          <input type="range" min={0.1} max={1} step={0.05} className="accent-amber-400" value={draft.volume ?? 0.8}
            onChange={(e) => set("volume", Number(e.target.value))} />
        </Row>
      </Section>

      <Section title="Room presence">
        <Row
          label="Home / Away"
          hint="The radar decides automatically; choosing Home or Away here always wins. Away only quiets the room (lights, speaker, screen), never the alarms themselves or your phone."
        >
          <select className={input} value={draft.presence_override ?? "auto"}
            onChange={(e) => set("presence_override", e.target.value as Settings["presence_override"])}>
            <option value="auto">Automatic (radar)</option>
            <option value="home">Home</option>
            <option value="away">Away</option>
          </select>
        </Row>
      </Section>

      <Section title="Morning report">
        <Row label="Email">
          <input className={input} type="email" autoComplete="email" value={draft.report_email ?? ""}
            onChange={(e) => set("report_email", e.target.value || null)} />
        </Row>
      </Section>

      <div className="fixed bottom-0 inset-x-0 z-10 bg-black border-t border-neutral-800 p-3">
        <div className="max-w-2xl mx-auto flex flex-col gap-1">
          {errors.length > 0 && <p className="text-red-400 text-sm">{errors[0]}</p>}
          {msg && (
            <p
              role="status"
              className={`text-sm ${msgTone === "ok" ? "text-green-400" : msgTone === "error" ? "text-red-400" : "text-amber-300"}`}
            >
              {msg}
            </p>
          )}
          <div className="flex gap-2">
            <button type="button" disabled={!dirty}
              className="px-4 py-3 rounded-lg border border-neutral-700 text-neutral-300 disabled:opacity-40"
              onClick={() => {
                setDirty(false);
                setPending(null);
                setMsg("");
                if (current) setDraft(current);
              }}>
              Undo
            </button>
            <button type="submit" disabled={!dirty || errors.length > 0 || sending || !!pending}
              className="flex-1 py-3 rounded-lg bg-amber-400 text-black font-bold disabled:opacity-40">
              Save
            </button>
          </div>
        </div>
      </div>
    </form>
    <FamilySection current={current} baseUrl={baseUrl} />
    <div className="h-28" aria-hidden />
    </>
  );
}
