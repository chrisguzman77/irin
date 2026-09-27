import { geoDistance, geoGraticule10, geoOrthographic, geoPath } from "d3-geo";
import type { GeoPermissibleObjects } from "d3-geo";
import { feature } from "topojson-client";
import land110 from "world-atlas/land-110m.json";
import { ZONES } from "./zones";

// The irin-* palette (src/index.css) as canvas colours.
const MINT = "#b1d2bd";
const CREAM = "#eeeeea";
const SAGE = "#a9b3a0";
const OCEAN = "#33352e";

const LAND = feature(land110, land110.objects.land) as GeoPermissibleObjects;
const GRATICULE = geoGraticule10();
const SPHERE: GeoPermissibleObjects = { type: "Sphere" };
const LABEL_MAX = (70 * Math.PI) / 180; // label cities within this angle of the centre

export type View = { lambda: number; phi: number }; // projection.rotate([lambda, phi])

// Size in CSS pixels; the canvas backing store is size * dpr.
export function drawGlobe(ctx: CanvasRenderingContext2D, size: number, dpr: number, view: View,
  selected: number, times: string[]) {
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, size, size);
  const r = size / 2 - 4;
  const c = size / 2;
  const projection = geoOrthographic().translate([c, c]).scale(r).rotate([view.lambda, view.phi]).clipAngle(90);
  const path = geoPath(projection, ctx);

  const shade = ctx.createRadialGradient(c - r * 0.35, c - r * 0.35, r * 0.1, c, c, r);
  shade.addColorStop(0, "#3d4036");
  shade.addColorStop(1, OCEAN);
  ctx.beginPath();
  path(SPHERE);
  ctx.fillStyle = shade;
  ctx.fill();

  ctx.beginPath();
  path(GRATICULE);
  ctx.strokeStyle = "rgba(238,238,234,0.08)";
  ctx.lineWidth = 0.6;
  ctx.stroke();

  ctx.beginPath();
  path(LAND);
  ctx.fillStyle = "rgba(127,156,111,0.55)";
  ctx.fill();
  ctx.strokeStyle = "rgba(177,210,189,0.3)";
  ctx.lineWidth = 0.5;
  ctx.stroke();

  ctx.beginPath();
  path(SPHERE);
  ctx.strokeStyle = "rgba(177,210,189,0.4)";
  ctx.lineWidth = 1;
  ctx.stroke();

  // Dots on the near side; labels for the selected city first, then outward
  // from the centre, skipping any label that would overlap one already drawn.
  const centre: [number, number] = [-view.lambda, -view.phi];
  const visible = ZONES.map((z, i) => ({ i, d: geoDistance([z.lon, z.lat], centre) }))
    .filter((v) => v.d < Math.PI / 2)
    .sort((a, b) => (a.i === selected ? -1 : b.i === selected ? 1 : a.d - b.d));
  const boxes: [number, number, number, number][] = [];
  const cityFont = "600 12px system-ui, -apple-system, sans-serif";
  const timeFont = "11px system-ui, -apple-system, sans-serif";
  ctx.textBaseline = "middle";

  for (const { i, d } of [...visible].reverse()) { // dots back to front, the selected on top
    const [x, y] = projection([ZONES[i].lon, ZONES[i].lat])!;
    const k = i === selected ? 10 : 4;
    boxes.push([x - k, y - k, x + k, y + k]); // no label covers a dot or the ring
    const fade = 1 - (d / (Math.PI / 2)) ** 2 * 0.7;
    ctx.globalAlpha = fade;
    ctx.beginPath();
    ctx.arc(x, y, i === selected ? 5 : 2.5, 0, 2 * Math.PI);
    ctx.fillStyle = i === selected ? MINT : CREAM;
    ctx.fill();
    if (i === selected) {
      ctx.beginPath();
      ctx.arc(x, y, 9, 0, 2 * Math.PI);
      ctx.strokeStyle = MINT;
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }
  }

  for (const { i, d } of visible) {
    if (d > LABEL_MAX && i !== selected) continue;
    const [x, y] = projection([ZONES[i].lon, ZONES[i].lat])!;
    ctx.font = cityFont;
    const cw = ctx.measureText(ZONES[i].city).width;
    ctx.font = timeFont;
    const tw = ctx.measureText(times[i]).width;
    const w = cw + 5 + tw;
    const h = 16;
    const gap = i === selected ? 14 : 8; // clears its own dot box (4) or ring box (10) plus the 3 px pad
    // Try right, left, above, below; the first spot inside the canvas that overlaps no label wins.
    const spots: [number, number][] = [[x + gap, y], [x - gap - w, y], [x - w / 2, y - h - 2], [x - w / 2, y + h + 2]];
    const spot = spots.map(([sx, sy]): [number, number, number, number, number, number] =>
      [sx - 3, sy - h / 2, sx + w + 3, sy + h / 2, sx, sy])
      .find((b) => b[0] >= 2 && b[2] <= size - 2 && b[1] >= 2 && b[3] <= size - 2
        && !boxes.some((o) => b[0] < o[2] && b[2] > o[0] && b[1] < o[3] && b[3] > o[1]));
    if (!spot) continue;
    const [bx0, by0, bx1, by1, lx, ly] = spot;
    const box: [number, number, number, number] = [bx0, by0, bx1, by1];
    boxes.push(box);
    ctx.globalAlpha = i === selected ? 1 : 1 - (d / LABEL_MAX) ** 2 * 0.6;
    ctx.fillStyle = "rgba(46,47,41,0.72)";
    ctx.beginPath();
    ctx.roundRect(box[0], box[1], box[2] - box[0], h, 4);
    ctx.fill();
    ctx.font = cityFont;
    ctx.fillStyle = i === selected ? MINT : CREAM;
    ctx.fillText(ZONES[i].city, lx, ly);
    ctx.font = timeFont;
    ctx.fillStyle = i === selected ? MINT : SAGE;
    ctx.fillText(times[i], lx + cw + 5, ly);
  }
  ctx.globalAlpha = 1;
}
