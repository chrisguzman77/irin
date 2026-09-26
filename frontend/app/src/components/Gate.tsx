import { useState } from "react";
import { pinWasRejected, savePin } from "../lib/usePin";

// A1: one input, the code. Kept in sessionStorage; a 401 on any call brings
// this screen back with the notice below.
export default function Gate() {
  const [draft, setDraft] = useState("");
  const rejected = pinWasRejected();
  return (
    <form
      className="min-h-dvh flex flex-col items-center justify-center gap-5 bg-black text-white px-6"
      onSubmit={(e) => {
        e.preventDefault();
        if (draft.trim()) savePin(draft.trim());
      }}
    >
      <h1 className="text-3xl font-bold tracking-tight">Irin</h1>
      <label htmlFor="code" className="text-lg text-neutral-300">
        Enter your Irin code
      </label>
      <input
        id="code"
        className="w-48 text-center text-3xl tracking-[0.4em] p-3 rounded-lg bg-neutral-900 border border-neutral-700 text-white"
        type="password"
        inputMode="numeric"
        autoComplete="off"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        autoFocus
      />
      {rejected && <p className="text-red-400 font-semibold">That code was not accepted. Try again.</p>}
      <button className="bg-amber-400 text-black font-semibold px-8 py-3 rounded-lg text-lg" type="submit">
        Open
      </button>
      <p className="text-xs text-neutral-500 text-center max-w-xs">
        The code stays in this tab only; closing the tab forgets it.
      </p>
    </form>
  );
}
