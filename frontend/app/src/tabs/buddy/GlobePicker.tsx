import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent, PointerEvent } from "react";
import { drawGlobe } from "./globe/draw";
import type { View } from "./globe/draw";
import { ZONES, fromYou, localTime, lonDelta, nearestIndex, startIndex } from "./globe/zones";

// The buddy-zone globe (relay/README.md "Buddy onboarding v2"). The interface is pinned:
// the wizard renders <GlobePicker initialZone value onChange />.
export type GlobePickerProps = {
  initialZone: string; // the user's home zone: the globe starts centred on it
  value: string | null; // the zone picked for the buddy (IANA name)
  onChange: (zone: string) => void;
};

const MAX_SIZE = 340;
const THROW_MS = 250; // inertia: how far a release carries, in ms of release velocity
const tilt = (lat: number) => -Math.max(-25, Math.min(30, lat * 0.6)); // projection phi for a city
const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// Drag horizontally to spin; a throw glides to a stop where it lands (no pull
// toward a city), and the city nearest the centre is the pick, highlighted live
// while spinning. Left/right arrows step one zone.
export default function GlobePicker({ initialZone, value, onChange }: GlobePickerProps) {
  const [selected, setSelected] = useState(() => startIndex(value ?? initialZone, new Date()));
  const [now, setNow] = useState(() => new Date());
  const [size, setSize] = useState(300);
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const view = useRef<View>({ lambda: -ZONES[selected].lon, phi: tilt(ZONES[selected].lat) });
  const anim = useRef(0);
  const frame = useRef(0);
  const drag = useRef<{ x0: number; lambda0: number; samples: { x: number; t: number }[] } | null>(null);
  const times = useMemo(() => ZONES.map((z) => localTime(z.zone, now)), [now]);
  const latest = useRef({ selected, times, size, onChange });
  useEffect(() => { latest.current = { selected, times, size, onChange }; });

  const paint = useCallback(() => {
    const ctx = canvasRef.current?.getContext("2d");
    if (!ctx) return;
    const { selected, times, size } = latest.current;
    drawGlobe(ctx, size, window.devicePixelRatio || 1, view.current, selected, times);
  }, []);
  const paintSoon = useCallback(() => {
    if (!frame.current) frame.current = requestAnimationFrame(() => { frame.current = 0; paint(); });
  }, [paint]);

  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 30_000);
    return () => { clearInterval(t); cancelAnimationFrame(anim.current); cancelAnimationFrame(frame.current); };
  }, []);

  useEffect(() => {
    const wrap = wrapRef.current;
    if (!wrap) return;
    const ro = new ResizeObserver(() => setSize(Math.max(200, Math.min(MAX_SIZE, Math.floor(wrap.clientWidth)))));
    ro.observe(wrap);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(size * dpr);
    canvas.height = Math.round(size * dpr);
    paint();
  }, [size, selected, times, paint]);

  // Turn to zone i starting from longitude-rotation `from` (the throw's resting point).
  const snapTo = useCallback((i: number, from: number) => {
    cancelAnimationFrame(anim.current);
    const target: View = { lambda: from + lonDelta(-ZONES[i].lon, from), phi: tilt(ZONES[i].lat) };
    setSelected(i);
    latest.current.selected = i;
    latest.current.onChange(ZONES[i].zone);
    if (reducedMotion()) {
      view.current = target;
      paint();
      return;
    }
    const start = { ...view.current };
    const ms = Math.min(900, 300 + Math.abs(target.lambda - start.lambda) * 4);
    const t0 = performance.now();
    const step = (t: number) => {
      const k = Math.min(1, (t - t0) / ms);
      const e = 1 - (1 - k) ** 3; // ease-out: starts fast like the throw, settles gently
      view.current = { lambda: start.lambda + (target.lambda - start.lambda) * e, phi: start.phi + (target.phi - start.phi) * e };
      paint();
      if (k < 1) anim.current = requestAnimationFrame(step);
    };
    anim.current = requestAnimationFrame(step);
  }, [paint]);

  // Highlight whichever city is nearest the centre right now (live, while spinning).
  const pickNearest = useCallback((commit: boolean) => {
    const i = nearestIndex(-view.current.lambda);
    if (i !== latest.current.selected) {
      latest.current.selected = i;
      setSelected(i);
    }
    if (commit) latest.current.onChange(ZONES[i].zone);
  }, []);

  // A throw's glide: ease out from here to `rest`, no snapping; the pick is where it stops.
  const glideTo = useCallback((rest: number) => {
    cancelAnimationFrame(anim.current);
    const start = view.current.lambda;
    if (reducedMotion() || Math.abs(rest - start) < 0.5) {
      view.current = { ...view.current, lambda: rest };
      paint();
      pickNearest(true);
      return;
    }
    const ms = Math.min(900, 250 + Math.abs(rest - start) * 5);
    const t0 = performance.now();
    const step = (t: number) => {
      const k = Math.min(1, (t - t0) / ms);
      const e = 1 - (1 - k) ** 3;
      view.current = { ...view.current, lambda: start + (rest - start) * e };
      paint();
      pickNearest(k >= 1);
      if (k < 1) anim.current = requestAnimationFrame(step);
    };
    anim.current = requestAnimationFrame(step);
  }, [paint, pickNearest]);

  const onPointerDown = (e: PointerEvent<HTMLCanvasElement>) => {
    cancelAnimationFrame(anim.current);
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { x0: e.clientX, lambda0: view.current.lambda, samples: [{ x: e.clientX, t: e.timeStamp }] };
  };
  const onPointerMove = (e: PointerEvent<HTMLCanvasElement>) => {
    const d = drag.current;
    if (!d) return;
    const r = size / 2 - 4;
    view.current = { ...view.current, lambda: d.lambda0 + ((e.clientX - d.x0) / r) * (180 / Math.PI) };
    d.samples.push({ x: e.clientX, t: e.timeStamp });
    while (d.samples.length > 2 && e.timeStamp - d.samples[0].t > 100) d.samples.shift();
    pickNearest(false);
    paintSoon();
  };
  const onPointerUp = (e: PointerEvent<HTMLCanvasElement>) => {
    const d = drag.current;
    if (!d) return;
    drag.current = null;
    let rest = view.current.lambda;
    const first = d.samples[0];
    const dt = e.timeStamp - first.t;
    if (!reducedMotion() && dt > 0 && e.timeStamp - d.samples[d.samples.length - 1].t < 80) {
      const degPerMs = ((e.clientX - first.x) / dt / (size / 2 - 4)) * (180 / Math.PI);
      rest += Math.max(-120, Math.min(120, degPerMs * THROW_MS));
    }
    glideTo(rest);
  };
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const dir = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!dir) return;
    e.preventDefault();
    snapTo((selected + dir + ZONES.length) % ZONES.length, view.current.lambda);
  };

  const z = ZONES[selected];
  return (
    <div className="flex flex-col items-center gap-3 select-none">
      <div ref={wrapRef} className="w-full flex flex-col items-center">
        <div aria-hidden className="text-irin-mint text-xs leading-none mb-1">▼</div>
        <div tabIndex={0} role="slider" aria-label="Your buddy's time zone" aria-valuemin={0}
          aria-valuemax={ZONES.length - 1} aria-valuenow={selected} aria-valuetext={`${z.city}, ${z.zone}`}
          onKeyDown={onKeyDown}
          className="rounded-full outline-none focus-visible:ring-2 focus-visible:ring-irin-mint">
          <canvas ref={canvasRef} style={{ width: size, height: size, touchAction: "pan-y" }}
            className="block cursor-grab active:cursor-grabbing"
            onPointerDown={onPointerDown} onPointerMove={onPointerMove}
            onPointerUp={onPointerUp} onPointerCancel={onPointerUp} />
        </div>
      </div>
      <div className="text-center" aria-live="polite">
        <div className="text-2xl font-semibold text-irin-mint">{z.city}</div>
        <div className="text-sm text-irin-sage">{z.zone.replace(/_/g, " ")}</div>
        <div className="text-4xl font-semibold tabular-nums text-irin-cream mt-1">{times[selected]}</div>
        <div className="text-sm text-irin-sage">{fromYou(z.zone, initialZone, now)}</div>
      </div>
      <div className="text-xs text-irin-sage/70">Drag the globe to spin it · ← → step one zone</div>
    </div>
  );
}
