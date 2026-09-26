import { useEffect, useRef, useState } from "react";
import { registerPrompter } from "../lib/freshPin";

// The prompt behind postFresh() (lib/freshPin.ts): mounted once in the app
// shell. Every high-stakes confirm opens it; what is typed goes to that one
// request and is then forgotten (state cleared, nothing stored).
export default function FreshPinPrompt() {
  const [ask, setAsk] = useState<{ title: string; resolve: (pin: string | null) => void } | null>(null);
  const [value, setValue] = useState("");
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    registerPrompter(
      (title) =>
        new Promise((resolve) => {
          setValue("");
          setAsk((prev) => {
            prev?.resolve(null); // a newer request cancels an older one
            return { title, resolve };
          });
        }),
    );
    return () => registerPrompter(null);
  }, []);

  useEffect(() => {
    if (ask) input.current?.focus();
  }, [ask]);

  if (!ask) return null;
  const finish = (pin: string | null) => {
    ask.resolve(pin);
    setAsk(null);
    setValue("");
  };

  return (
    <div role="dialog" aria-modal="true" aria-label={ask.title} className="fixed inset-0 z-50 bg-black/80 flex items-center justify-center p-6">
      <form
        className="w-full max-w-xs bg-neutral-900 border border-neutral-700 rounded-2xl p-5 flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (value) finish(value);
        }}
      >
        <h2 className="text-lg font-semibold text-center">{ask.title}</h2>
        <p className="text-xs text-neutral-400 text-center">Asked every time for this step; it is never saved.</p>
        <input
          ref={input}
          aria-label="PIN"
          className="text-center text-3xl tracking-[0.4em] p-3 rounded-lg bg-black border border-neutral-700 text-white"
          type="password"
          inputMode="numeric"
          autoComplete="off"
          maxLength={8}
          value={value}
          onChange={(e) => setValue(e.target.value.replace(/\D/g, ""))}
        />
        <div className="flex gap-2">
          <button type="button" className="flex-1 rounded-lg border border-neutral-700 py-3 text-neutral-300" onClick={() => finish(null)}>
            Cancel
          </button>
          <button type="submit" disabled={!value} className="flex-1 rounded-lg bg-amber-400 text-black font-bold py-3 disabled:opacity-40">
            Confirm
          </button>
        </div>
      </form>
    </div>
  );
}
