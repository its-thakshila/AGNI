import type { BBox } from '../api/types';

const DAY = 86_400_000;
export const pad = (n: number) => String(n).padStart(2, '0');
export const daysInMonth = (y: number, m: number) => new Date(Date.UTC(y, m, 0)).getUTCDate();
export const ymd = (y: number, m: number, d: number) => `${y}-${pad(m)}-${pad(d)}`;
export const ym = (y: number, m: number) => `${y}-${pad(m)}`;
export const parse = (s: string) => Date.parse(`${s}T00:00:00Z`);
export const iso = (t: number) => new Date(t).toISOString().slice(0, 10);
export const addDays = (s: string, n: number) => iso(parse(s) + n * DAY);
export const daysBetween = (a: string, b: string) => Math.round((parse(b) - parse(a)) / DAY) + 1;
export const minDate = (a: string, b: string) => (a < b ? a : b);
export const maxDate = (a: string, b: string) => (a > b ? a : b);

export function* eachDay(start: string, end: string) {
  for (let t = parse(start), e = parse(end); t <= e; t += DAY) yield iso(t);
}

/** First/last fire-day of a month, clipped to [recordStart, latest]. */
export function monthRange(y: number, m: number, recordStart: string, latest: string): { start: string; end: string } | null {
  const start = maxDate(ymd(y, m, 1), recordStart);
  const end = minDate(ymd(y, m, daysInMonth(y, m)), latest);
  return start <= end ? { start, end } : null;
}

export const boxAreaDeg2 = (b: BBox) => (b.max_lat - b.min_lat) * (b.max_lon - b.min_lon);

/** Clip a box to the coverage box; null if they don't overlap. */
export function clipBox(b: BBox, cov: BBox): BBox | null {
  const r = {
    min_lat: Math.max(b.min_lat, cov.min_lat),
    max_lat: Math.min(b.max_lat, cov.max_lat),
    min_lon: Math.max(b.min_lon, cov.min_lon),
    max_lon: Math.min(b.max_lon, cov.max_lon),
  };
  return r.min_lat < r.max_lat && r.min_lon < r.max_lon ? r : null;
}
