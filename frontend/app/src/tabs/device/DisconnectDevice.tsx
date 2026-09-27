import { useState } from "react";
import { unpairOwnerDevice } from "../../lib/owner";

// A2: unpair this phone from its Irin. Confirms inline (no window.confirm);
// clears the phone's stored pairing even if the relay call fails, showing a
// note when it does. DeviceTab hides this in dev-override mode, since there
// is no real pairing to drop there.
export default function DisconnectDevice() {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");

  return (
    <fieldset className="border border-neutral-800 rounded-xl px-4 py-2 mb-4">
      <legend className="px-1 text-neutral-400 text-xs uppercase tracking-wider">This phone</legend>
      {note ? (
        <p className="text-sm text-neutral-300 py-2">{note}</p>
      ) : !confirming ? (
        <button
          type="button"
          className="w-full my-2 rounded-lg px-3 py-2 border border-red-900 text-red-400"
          onClick={() => setConfirming(true)}
        >
          Disconnect this phone
        </button>
      ) : (
        <div className="flex flex-col gap-2 py-2">
          <p className="text-sm text-neutral-300">
            This phone will stop talking to your Irin until it is paired again.
          </p>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={busy}
              className="flex-1 rounded-lg px-3 py-2 bg-red-500 text-black font-semibold disabled:opacity-40"
              onClick={async () => {
                setBusy(true);
                const res = await unpairOwnerDevice();
                setBusy(false);
                if (!res.ok) setNote(res.note);
                // on success the owner store fires; useDeviceTarget flips to
                // "unpaired" and this whole screen is replaced.
              }}
            >
              {busy ? "Disconnecting…" : "Yes, disconnect"}
            </button>
            <button
              type="button"
              disabled={busy}
              className="rounded-lg px-3 py-2 border border-neutral-700 text-neutral-300"
              onClick={() => setConfirming(false)}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </fieldset>
  );
}
