import { useState } from "react";
import { createViewLink, revokeViewLink, useViewLinks } from "../../lib/familyView";

// A Level 2 recipient's view link (family/ page): create it (shown once, to
// copy and send), see that one is on, turn it off. Pausing, revoking, or
// moving the recipient to Story only turns it off too (FamilySection).
export default function FamilyViewLink({ recipientId, name, demo, disabled }: { recipientId: string; name: string; demo: boolean; disabled: boolean }) {
  const links = useViewLinks();
  const link = links[recipientId];
  const [fresh, setFresh] = useState<string | null>(null); // the URL, only right after creating it
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const btn = "rounded-lg px-3 py-2 text-sm font-medium disabled:opacity-40";

  const create = async () => {
    setBusy(true);
    setMsg(null);
    const res = await createViewLink(recipientId, demo);
    setBusy(false);
    if (res.ok) {
      setFresh(res.url);
      setCopied(false);
    } else setMsg(res.reason);
  };
  const off = async () => {
    setBusy(true);
    setMsg(null);
    const res = await revokeViewLink(recipientId);
    setBusy(false);
    setFresh(null);
    setMsg(res.ok ? `${name}'s view link is off.` : res.reason);
  };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(fresh ?? "");
      setCopied(true);
    } catch {
      setMsg("Could not copy: select the link and copy it by hand.");
    }
  };

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-neutral-800 p-2">
      <span className="text-xs uppercase tracking-wider text-neutral-500">Live view link</span>
      {fresh ? (
        <>
          <span className="text-sm">
            Send this link to {name}. It is shown only now{demo ? " (a DEMO link: it shows replayed data)" : ""}.
          </span>
          <code className="text-xs break-all bg-neutral-950 rounded p-2 select-all">{fresh}</code>
          <div className="flex gap-2">
            <button type="button" className={`${btn} flex-1 bg-white text-black`} onClick={copy}>{copied ? "Copied" : "Copy link"}</button>
            <button type="button" className={`${btn} border border-neutral-700 text-neutral-300`} onClick={() => setFresh(null)}>Done</button>
          </div>
        </>
      ) : link ? (
        <div className="flex items-center gap-2">
          <span className="text-sm flex-1">
            On since {link.created_at.slice(0, 10)}{link.is_demo ? " · DEMO" : ""}. {name} sees the current number and last night.
          </span>
          <button type="button" disabled={busy || disabled} className={`${btn} border border-red-800 text-red-300`} onClick={off}>Turn off</button>
        </div>
      ) : (
        <div className="flex items-center gap-2">
          <span className="text-sm text-neutral-400 flex-1">No live view. The story comes by email either way.</span>
          <button type="button" disabled={busy || disabled} className={`${btn} bg-neutral-800 text-neutral-200`} onClick={create}>Create view link</button>
        </div>
      )}
      {msg && <span className="text-sm text-amber-300">{msg}</span>}
    </div>
  );
}
