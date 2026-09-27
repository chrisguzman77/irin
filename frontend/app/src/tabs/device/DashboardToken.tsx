import { useState } from "react";
import { ownerBearer, setOwnerBearer } from "../../lib/dash";

// The owner's dashboard token for My Irin (the cloud's OWNER_BEARER), typed
// once and kept in this browser's localStorage until the A2 owner pairing
// decides how the app receives it. It is never sent to your Irin, only to
// Irin Cloud's dashboards, and never shown back in full.
export default function DashboardToken() {
  const [has, setHas] = useState(() => !!ownerBearer());
  const [value, setValue] = useState("");
  const [msg, setMsg] = useState("");
  return (
    <fieldset className="border border-neutral-800 rounded-xl px-4 py-2 mb-4">
      <legend className="px-1 text-neutral-400 text-xs uppercase tracking-wider">My Irin dashboards</legend>
      <label className="flex flex-col gap-1 py-2">
        <span className="text-sm text-neutral-300">Dashboard token</span>
        <input
          className="bg-neutral-900 border border-neutral-700 rounded-lg px-3 py-2 text-white w-full"
          type="password"
          autoComplete="off"
          placeholder={has ? "saved on this phone" : "paste the token"}
          value={value}
          onChange={(e) => {
            setValue(e.target.value);
            setMsg("");
          }}
        />
        <span className="text-xs text-neutral-500">Kept only in this browser; the My Irin tab reads your dashboards with it.</span>
      </label>
      <div className="flex gap-2 pb-2">
        <button
          type="button"
          disabled={!value.trim()}
          className="flex-1 rounded-lg px-3 py-2 bg-white text-black font-semibold disabled:opacity-40"
          onClick={() => {
            setOwnerBearer(value.trim());
            setHas(!!ownerBearer());
            setValue("");
            setMsg(ownerBearer() ? "Token saved on this phone." : "This browser would not keep it.");
          }}
        >
          Save token
        </button>
        {has && (
          <button
            type="button"
            className="rounded-lg px-3 py-2 border border-neutral-700 text-neutral-300"
            onClick={() => {
              setOwnerBearer(null);
              setHas(false);
              setMsg("Token removed from this phone.");
            }}
          >
            Forget
          </button>
        )}
      </div>
      {msg && <p role="status" className="text-sm text-emerald-400 pb-2">{msg}</p>}
    </fieldset>
  );
}
