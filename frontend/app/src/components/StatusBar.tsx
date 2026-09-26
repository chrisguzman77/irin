import { useDevice } from "../lib/device";

// Honesty badges on every screen: DEMO while the device replays (invariant 1),
// DEV when the Device tab uses the local override, "clock not set" until the
// Pi's NTP sync, and a banner when the socket has been down > 15 s.
export default function StatusBar() {
  const { target, socket } = useDevice();
  const snap = socket.snapshot;
  const demo = snap?.mode === "replay";
  const dev = target.status === "ready" && target.dev;
  const clockUnset = snap ? snap.clock_synced === false : false;
  if (!demo && !dev && !clockUnset && !socket.disconnectedLong) return null;
  return (
    <div className="sticky top-0 z-40">
      {demo && (
        <div className="bg-amber-400 text-black text-center font-black tracking-widest py-1.5 text-lg">
          DEMO — replayed data, not live
        </div>
      )}
      {socket.disconnectedLong && (
        <div className="bg-red-600 text-white text-center font-bold py-1.5">
          Disconnected from your Irin — numbers may be old
        </div>
      )}
      {(dev || clockUnset) && (
        <div className="flex gap-2 justify-end px-3 py-1 bg-neutral-950">
          {clockUnset && (
            <span className="text-xs border border-neutral-500 text-neutral-200 px-2 py-0.5 rounded">clock not set</span>
          )}
          {dev && target.status === "ready" && (
            <span className="text-xs bg-sky-300 text-black px-2 py-0.5 rounded font-semibold">DEV · {target.url}</span>
          )}
        </div>
      )}
    </div>
  );
}
