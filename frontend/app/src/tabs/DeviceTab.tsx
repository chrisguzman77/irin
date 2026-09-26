import { DEVICE_URL_OVERRIDE } from "../config";

// justin.md A2: "Pair your Irin" — scan the kiosk QR or type the 6-digit code, POST {code, username}
// to the relay's /v0/device/pair, store device_url + token, then every Device-tab call goes to
// device_url with the token and the PIN. Placeholder.
export default function DeviceTab() {
  return (
    <section className="flex flex-col gap-4">
      <h2 className="text-xl">Irin Device</h2>
      {DEVICE_URL_OVERRIDE && (
        <span className="self-start bg-amber-400 text-black text-xs px-2 py-1 rounded">DEV · {DEVICE_URL_OVERRIDE}</span>
      )}
      <button className="bg-amber-400 text-black px-4 py-3 rounded text-lg" onClick={() => alert("Pairing: A2")}>
        Pair your Irin
      </button>
      <p className="text-neutral-400">Live view, acknowledge, settings, logging, reports, demo panel: placeholders.</p>
    </section>
  );
}
