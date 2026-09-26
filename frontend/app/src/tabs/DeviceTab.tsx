import { useDevice } from "../lib/device";
import LiveView from "./device/LiveView";

// The Device tab. A2 (pairing) is not built yet: without the development
// override it shows the pairing entry point.
export default function DeviceTab() {
  const { target, socket } = useDevice();

  if (target.status === "resolving") return <p className="text-neutral-400">Looking for your Irin…</p>;
  if (target.status === "unpaired") {
    return (
      <section className="flex flex-col gap-4">
        <h2 className="text-xl font-semibold">Irin Device</h2>
        <button className="bg-amber-400 text-black px-4 py-3 rounded-lg text-lg font-semibold" disabled>
          Pair your Irin
        </button>
        <p className="text-neutral-400 text-sm">Pairing arrives with step A2.</p>
      </section>
    );
  }
  return <LiveView snap={socket.snapshot} baseUrl={target.url} stale={socket.disconnectedLong} />;
}
