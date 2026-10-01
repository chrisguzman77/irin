import { useState } from "react";
import { verifyAccount } from "../../lib/account";

// Phone-only accounts, Phase 1: the "Connect your CGM" step after sign-up.
// Irin Cloud reads ONE entry from the Nightscout feed to see it is live and
// discards the value; the URL and token are kept encrypted in the cloud and
// never reach the relay (invariant 20). A 422's detail is shown as the cloud
// wrote it ("feed unreachable", "feed refused the token", "no reading in the
// last 15 minutes").
const input = "bg-neutral-900 border border-neutral-700 rounded-lg px-3 py-2 text-white";
const URL_RE = /^https?:\/\/[^\s/?#]+(\/[^\s?#]*)?$/;

export default function ConnectCgm({ onVerified, onStartOver }: { onVerified: () => void; onStartOver: () => void }) {
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const urlOk = URL_RE.test(url.trim()) && url.trim().length <= 200;
  const tokenOk = token.length > 0 && token.length <= 200;

  const verify = async () => {
    setBusy(true);
    setErr("");
    const r = await verifyAccount(url.trim().replace(/\/$/, ""), token);
    setBusy(false);
    if (r.ok) onVerified();
    else setErr(r.reason);
  };

  return (
    <div className="flex flex-col gap-3">
      <h3 className="text-lg font-semibold">Connect your CGM</h3>
      <p className="text-sm text-neutral-300">
        Irin checks once that your Nightscout feed is live, then you can be matched. The reading it sees is thrown away;
        your feed address and token stay in Irin Cloud and never reach the buddy relay.
      </p>
      <label className="flex flex-col gap-1 text-sm text-neutral-300">
        Nightscout URL
        <input className={input} type="url" inputMode="url" autoComplete="off" placeholder="https://your-site.example.com"
          value={url} onChange={(e) => { setUrl(e.target.value); setErr(""); }} />
      </label>
      <label className="flex flex-col gap-1 text-sm text-neutral-300">
        Nightscout token
        <input className={input} type="password" autoComplete="off" value={token}
          onChange={(e) => { setToken(e.target.value); setErr(""); }} />
      </label>
      <p className="text-xs text-neutral-500">The address without a query string; the token is the one your Nightscout site issued.</p>
      <button type="button" disabled={busy || !urlOk || !tokenOk}
        className="rounded-lg px-3 py-3 bg-sky-400 text-black font-semibold disabled:opacity-40" onClick={verify}>
        {busy ? "Checking your feed…" : "Connect"}
      </button>
      {err && <p role="status" className="text-sm text-red-400">{err}</p>}
      <button type="button" className="self-start text-sm text-neutral-400 underline" onClick={onStartOver}>
        Start over with a new profile
      </button>
    </div>
  );
}
