// PLACEHOLDER until the globe session lands (relay/README.md "Buddy onboarding v2").
// The interface is pinned: the wizard renders <GlobePicker initialZone value onChange />
// and the globe session replaces only this file's body, never its props.
export type GlobePickerProps = {
  initialZone: string; // the user's home zone: the globe starts centred on it
  value: string | null; // the zone picked for the buddy (IANA name)
  onChange: (zone: string) => void;
};

const ZONES = ["America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles", "Europe/London",
  "Europe/Berlin", "Asia/Kolkata", "Asia/Tokyo", "Australia/Sydney"];

export default function GlobePicker({ initialZone, value, onChange }: GlobePickerProps) {
  return (
    <select className="bg-irin-surface rounded-lg px-3 py-2" value={value ?? initialZone}
      onChange={(e) => onChange(e.target.value)}>
      {ZONES.map((z) => <option key={z} value={z}>{z.replace("_", " ")}</option>)}
    </select>
  );
}
