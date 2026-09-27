// The globe's zones: one representative city per zone at its real lon/lat.
// Sorted west to east, and spaced at least ~5 degrees of longitude apart,
// because the globe snaps to the city nearest the centre by longitude.
export type Zone = { zone: string; city: string; lon: number; lat: number };

export const ZONES: Zone[] = [
  { zone: "Pacific/Pago_Pago", city: "Pago Pago", lon: -170.7, lat: -14.28 },
  { zone: "Pacific/Honolulu", city: "Honolulu", lon: -157.86, lat: 21.31 },
  { zone: "America/Anchorage", city: "Anchorage", lon: -149.9, lat: 61.22 },
  { zone: "America/Los_Angeles", city: "Los Angeles", lon: -118.24, lat: 34.05 },
  { zone: "America/Denver", city: "Denver", lon: -104.99, lat: 39.74 },
  { zone: "America/Chicago", city: "Chicago", lon: -87.63, lat: 41.88 },
  { zone: "America/New_York", city: "New York", lon: -74.01, lat: 40.71 },
  { zone: "America/Halifax", city: "Halifax", lon: -63.57, lat: 44.65 },
  { zone: "America/St_Johns", city: "St. John's", lon: -52.71, lat: 47.56 },
  { zone: "America/Sao_Paulo", city: "São Paulo", lon: -46.63, lat: -23.55 },
  { zone: "Atlantic/Azores", city: "Ponta Delgada", lon: -25.67, lat: 37.74 },
  { zone: "Africa/Dakar", city: "Dakar", lon: -17.47, lat: 14.69 },
  { zone: "Europe/London", city: "London", lon: -0.13, lat: 51.51 },
  { zone: "Europe/Berlin", city: "Berlin", lon: 13.4, lat: 52.52 },
  { zone: "Africa/Cairo", city: "Cairo", lon: 31.24, lat: 30.04 },
  { zone: "Europe/Moscow", city: "Moscow", lon: 37.62, lat: 55.76 },
  { zone: "Asia/Riyadh", city: "Riyadh", lon: 46.68, lat: 24.71 },
  { zone: "Asia/Dubai", city: "Dubai", lon: 55.27, lat: 25.2 },
  { zone: "Asia/Karachi", city: "Karachi", lon: 67.0, lat: 24.86 },
  { zone: "Asia/Kolkata", city: "Delhi", lon: 77.21, lat: 28.61 },
  { zone: "Asia/Dhaka", city: "Dhaka", lon: 90.41, lat: 23.81 },
  { zone: "Asia/Bangkok", city: "Bangkok", lon: 100.5, lat: 13.76 },
  { zone: "Australia/Perth", city: "Perth", lon: 115.86, lat: -31.95 },
  { zone: "Asia/Shanghai", city: "Shanghai", lon: 121.47, lat: 31.23 },
  { zone: "Asia/Seoul", city: "Seoul", lon: 126.98, lat: 37.57 },
  { zone: "Asia/Tokyo", city: "Tokyo", lon: 139.69, lat: 35.69 },
  { zone: "Australia/Sydney", city: "Sydney", lon: 151.21, lat: -33.87 },
  { zone: "Pacific/Auckland", city: "Auckland", lon: 174.76, lat: -36.85 },
];

const partsFmt = new Map<string, Intl.DateTimeFormat>();
const timeFmt = new Map<string, Intl.DateTimeFormat>();

function cached(map: Map<string, Intl.DateTimeFormat>, zone: string, opts: Intl.DateTimeFormatOptions) {
  let f = map.get(zone);
  if (!f) {
    try {
      f = new Intl.DateTimeFormat("en-US", { ...opts, timeZone: zone });
    } catch {
      f = new Intl.DateTimeFormat("en-US", { ...opts, timeZone: "UTC" }); // an unknown zone reads as UTC
    }
    map.set(zone, f);
  }
  return f;
}

// "3:05 PM" in the zone, 12-hour.
export function localTime(zone: string, now: Date): string {
  return cached(timeFmt, zone, { hour: "numeric", minute: "2-digit", hour12: true }).format(now);
}

// The zone's UTC offset in minutes at `now` (DST-aware).
export function offsetMinutes(zone: string, now: Date): number {
  const f = cached(partsFmt, zone, {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  });
  const p: Record<string, number> = {};
  for (const { type, value } of f.formatToParts(now)) p[type] = Number(value);
  const asUtc = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute);
  return Math.round((asUtc - Math.floor(now.getTime() / 60000) * 60000) / 60000);
}

// "+3 h from you", "-9 h 30 min from you", "same time as you".
export function fromYou(zone: string, home: string, now: Date): string {
  const d = offsetMinutes(zone, now) - offsetMinutes(home, now);
  if (d === 0) return "same time as you";
  const a = Math.abs(d);
  const h = Math.floor(a / 60);
  const m = a % 60;
  return `${d > 0 ? "+" : "-"}${h} h${m ? ` ${m} min` : ""} from you`;
}

// Where the globe starts: the zone itself, else a listed zone with the same
// offset right now, else the closest offset.
export function startIndex(zone: string, now: Date): number {
  const exact = ZONES.findIndex((z) => z.zone === zone);
  if (exact >= 0) return exact;
  const want = offsetMinutes(zone, now);
  let best = 0;
  for (let i = 1; i < ZONES.length; i++) {
    if (Math.abs(offsetMinutes(ZONES[i].zone, now) - want) < Math.abs(offsetMinutes(ZONES[best].zone, now) - want)) best = i;
  }
  return best;
}

// Signed longitude difference a - b wrapped into (-180, 180].
export function lonDelta(a: number, b: number): number {
  const d = (((a - b) % 360) + 540) % 360 - 180;
  return d === -180 ? 180 : d;
}

// Index of the zone whose city is nearest longitude `lon`.
export function nearestIndex(lon: number): number {
  let best = 0;
  for (let i = 1; i < ZONES.length; i++) {
    if (Math.abs(lonDelta(ZONES[i].lon, lon)) < Math.abs(lonDelta(ZONES[best].lon, lon))) best = i;
  }
  return best;
}
