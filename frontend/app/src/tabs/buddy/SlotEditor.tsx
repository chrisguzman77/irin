import type { Slot } from "../../lib/buddy";

// The availability slot editor (my local time), shared by ProfileForm and the
// onboarding wizard's step 3.
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const input = "bg-neutral-900 border border-neutral-700 rounded-lg px-3 py-2 text-white";

export default function SlotEditor({ slots, onChange }: { slots: Slot[]; onChange: (s: Slot[]) => void }) {
  const setSlot = (i: number, patch: Partial<Slot>) => onChange(slots.map((s, j) => (j === i ? { ...s, ...patch } : s)));
  return (
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
          <button type="button" aria-label="Remove" className="px-2 text-red-300" onClick={() => onChange(slots.filter((_, j) => j !== i))}>
            ×
          </button>
        </div>
      ))}
      <button type="button" className="self-start rounded-lg px-3 py-2 bg-neutral-900 text-neutral-200"
        onClick={() => onChange([...slots, { weekday: 0, start: "22:00", end: "06:00" }])}>
        Add hours
      </button>
    </div>
  );
}
