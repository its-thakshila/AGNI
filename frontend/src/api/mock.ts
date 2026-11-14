// In-browser stand-in for the AGNI API (contract v1.1). Numbers are shaped like the
// Australian record (northern dry-season burning, southern summer fires, Black
// Summer 2019-20) but they are SYNTHETIC. Every endpoint is derived from one
// per-fire-day series, so calendar, anomaly, daily, grid and CSV always agree.

import type {
  AnomalyData, AreaQuery, BBox, CalendarCell, CalendarData, Category, Climatology, DailyData, DailyValue,
  Envelope, GridCell, GridData, HasData, Meta, Origin, Warning,
} from './types';
import { ApiError } from './errors';
import { addDays, boxAreaDeg2, daysBetween, daysInMonth, eachDay, pad, ymd } from '../lib/dates';

const RECORD_START = '2000-11-01';
const LATEST = new Date(Date.now() - 86_400_000).toISOString().slice(0, 10); // yesterday
const COVERAGE: BBox = { min_lat: -44, max_lat: -9, min_lon: 112, max_lon: 154 };
const KNOWN_GAPS: Array<[string, string]> = [['2001-06-17', '2001-07-02'], ['2002-03-21', '2002-03-27']];
const VIIRS_GAPS: Array<[string, string]> = [
  ['2012-01-01', '2012-01-19'], ['2012-03-25', '2012-03-26'], ['2022-06-28', '2022-06-28'], ['2022-07-27', '2022-08-10'],
  ['2023-07-26', '2023-07-27'], ['2024-05-30', '2024-06-03'], ['2024-07-10', '2024-07-15'], ['2024-07-25', '2024-07-28'],
  ['2024-09-26', '2024-09-27'], ['2024-11-03', '2024-11-05'], ['2025-11-12', '2025-11-12'],
];
const LIMITS: Meta['limits'] = {
  hotspots_max_limit: 20000, small_box_deg: 0.5, hotspots_max_deg2_days: 4000, csv_max_deg2_days: 20000, grid_max_days: 366,
};
const inRanges = (d: string, rs: Array<[string, string]>) => rs.some(([a, b]) => d >= a && d <= b);
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

// ---------- deterministic randomness ----------
function hash(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return h >>> 0;
}
function rng(seed: string) {
  let a = hash(seed);
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// ---------- origins ----------
function originOf(d: string): Origin {
  if (inRanges(d, VIIRS_GAPS)) return 'gapfill';
  if (d < '2012-01-01') return 'ml';
  if (d <= '2025-12-31') return 'real';
  return 'firms_nrt';
}
const isModel = (o: Origin) => o === 'ml' || o === 'gapfill';
function uncertaintyOf(d: string, o: Origin): number {
  if (o === 'ml') return d < '2002-07-04' ? 0.16 : 0.11;
  if (o === 'gapfill') return d < '2013-01-01' ? 0.11 : 0.13;
  return 0;
}

// ---------- climate bands ----------
interface Band { id: string; lat: [number, number]; peak: number; season: number[] }
const BANDS: Band[] = [
  { id: 'north', lat: [-20, -9], peak: 110_000, season: [0.15, 0.1, 0.15, 0.35, 0.7, 0.9, 1, 1, 0.95, 0.75, 0.4, 0.2] },
  { id: 'central', lat: [-30, -20], peak: 26_000, season: [0.4, 0.3, 0.25, 0.2, 0.2, 0.25, 0.35, 0.55, 0.8, 1, 0.9, 0.6] },
  { id: 'south', lat: [-44, -30], peak: 16_000, season: [0.9, 1, 0.8, 0.5, 0.4, 0.2, 0.15, 0.15, 0.2, 0.35, 0.6, 0.85] },
];
const LON: [number, number] = [113, 154];

function yearEffect(band: string, y: number, m: number): number {
  if (band === 'south') {
    if (y === 2019 && m === 11) return 3;
    if (y === 2019 && m === 12) return 6;
    if (y === 2020 && m === 1) return 6;
    if (y === 2020 && m === 2) return 2;
    if (y === 2003 && m === 1) return 2.5;
    if (y === 2009 && m === 2) return 2;
  }
  if (band === 'central' && ((y === 2011 && m >= 9) || y === 2012)) return 2;
  if (band === 'north' && y === 2023) return 1.3;
  return 1;
}
function overlapFrac(b: BBox, band: Band): number {
  const lonO = Math.max(0, Math.min(b.max_lon, LON[1]) - Math.max(b.min_lon, LON[0]));
  const latO = Math.max(0, Math.min(b.max_lat, band.lat[1]) - Math.max(b.min_lat, band.lat[0]));
  return (lonO * latO) / ((LON[1] - LON[0]) * (band.lat[1] - band.lat[0]));
}
const TYPE_SHARE: Record<number, number> = { 0: 0.989, 2: 0.01, 3: 0.001 };

function bandDay(band: Band, d: string): number {
  const y = +d.slice(0, 4), m = +d.slice(5, 7);
  const monthNoise = 0.75 + rng(`${band.id}-${y}-${m}`)() * 0.55;
  const dayNoise = 0.35 + 1.3 * rng(`${band.id}-${d}`)(); // mean ~1
  return (band.peak * band.season[m - 1] * yearEffect(band.id, y, m) * monthNoise * dayNoise) / daysInMonth(y, m);
}

// ---------- the one series everything derives from ----------
type Series = Map<string, number | null>;
const cache = new Map<string, Series>();
function series(b: BBox, types: number[]): Series {
  const key = JSON.stringify([b, types]);
  const hit = cache.get(key);
  if (hit) return hit;
  const tf = types.reduce((s, t) => s + (TYPE_SHARE[t] ?? 0), 0);
  const fracs = BANDS.map((band) => overlapFrac(b, band));
  const out: Series = new Map();
  for (const d of eachDay(RECORD_START, LATEST)) {
    if (inRanges(d, KNOWN_GAPS)) { out.set(d, null); continue; }
    let v = 0;
    BANDS.forEach((band, i) => { if (fracs[i]) v += bandDay(band, d) * fracs[i]; });
    const w = isModel(originOf(d)) ? 1.08 : 1; // model rows carry weight > 1
    out.set(d, +(v * tf * w).toFixed(1));
  }
  cache.set(key, out);
  return out;
}

// ---------- validation, envelope, warnings ----------
function checkBox(b: BBox) {
  if (b.min_lat === b.max_lat || b.min_lon === b.max_lon) throw new ApiError('EMPTY_BOX', 'The box has zero width or height.', 400);
  if (b.min_lat < COVERAGE.min_lat || b.max_lat > COVERAGE.max_lat || b.min_lon < COVERAGE.min_lon || b.max_lon > COVERAGE.max_lon)
    throw new ApiError('BOX_OUTSIDE_COVERAGE', 'The selected area must be inside Australia (lon 112 to 154, lat -44 to -9).', 400, 'lon2');
}
function checkRange(start: string, end: string) {
  if (start > end || start < RECORD_START || end > LATEST)
    throw new ApiError('INVALID_DATE_RANGE', `Dates must be between ${RECORD_START} and ${LATEST}.`, 400, 'start');
}

function warningsFor(b: BBox, start: string, end: string): Warning[] {
  const w: Warning[] = [];
  let model = false, outage = false, gapRecent = false;
  for (const d of eachDay(start, end)) {
    const o = originOf(d);
    if (isModel(o)) model = true;
    if (o === 'gapfill' && d >= '2022-01-01') gapRecent = true;
    if (inRanges(d, KNOWN_GAPS)) outage = true;
  }
  const small = b.max_lat - b.min_lat < LIMITS.small_box_deg && b.max_lon - b.min_lon < LIMITS.small_box_deg;
  if (model) w.push({ code: 'MODEL_DATA_INCLUDED', message: 'Part of this period is harmonized from MODIS by Harmonic, AGNI’s harmonization model.' });
  if (model && small) w.push({ code: 'SMALL_BOX_MODEL_PERIOD', message: 'Model-based years are reliable only as totals for small areas.' });
  if (outage) w.push({ code: 'KNOWN_OUTAGE', message: 'Some days have no satellite data at all (MODIS outage).' });
  if (gapRecent) w.push({ code: 'DAYNIGHT_UNRELIABLE', message: 'Day/night split is not shown on gap-filled days.' });
  if (end >= addDays(LATEST, -2)) w.push({ code: 'RECENT_DATA_PROVISIONAL', message: 'The last 3 days are near-real-time data and may still change.' });
  return w;
}

function envelope<T>(b: BBox, types: number[], data: T, extra: { start?: string; end?: string; missing?: string[]; warnings?: Warning[]; query?: object } = {}): Envelope<T> {
  const missing = extra.missing ?? [];
  return {
    query: { bbox: b, start: extra.start, end: extra.end, types, ...extra.query },
    partial: missing.length > 0,
    missing_dates: missing,
    warnings: extra.warnings ?? [],
    generated_at: new Date().toISOString().slice(0, 19) + 'Z',
    data,
  };
}

function quantile(sorted: number[], q: number): number {
  if (!sorted.length) return 0;
  const pos = (sorted.length - 1) * q;
  const lo = Math.floor(pos), hi = Math.ceil(pos);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
}
const r1 = (n: number) => Math.round(n * 10) / 10;

function categoryOf(v: number, s: { p10: number; p25: number; p75: number; p90: number }): Category {
  if (v < s.p10) return 'well_below';
  if (v < s.p25) return 'below';
  if (v <= s.p75) return 'normal';
  if (v <= s.p90) return 'above';
  return 'well_above';
}

// ---------- endpoints ----------
export async function meta(): Promise<Meta> {
  await wait(80);
  return {
    coverage: { record_start: RECORD_START, local_end: '2025-12-31', latest_fire_date: LATEST, bbox: COVERAGE },
    periods: [
      { from: '2000-11-01', to: '2002-07-03', origin: 'ml', label: 'Modelled from MODIS Terra', uncertainty: 0.16 },
      { from: '2002-07-04', to: '2011-12-31', origin: 'ml', label: 'Modelled from MODIS Terra + Aqua', uncertainty: 0.11 },
      { from: '2012-01-01', to: '2025-12-31', origin: 'real', label: 'VIIRS S-NPP observations', uncertainty: 0 },
      { from: '2026-01-01', to: null, origin: 'firms_nrt', label: 'VIIRS S-NPP via NASA FIRMS', uncertainty: 0 },
    ],
    known_gaps: KNOWN_GAPS.map(([from, to]) => ({ from, to, reason: 'MODIS outage' })),
    types: [
      { value: 0, label: 'Vegetation fire' },
      { value: 2, label: 'Static / industrial source' },
      { value: 3, label: 'Offshore' },
    ],
    limits: LIMITS,
    firms: { status: 'ok', last_fetch: new Date().toISOString().slice(0, 19) + 'Z' },
    contract_version: '1.1',
  };
}

export async function daily(q: AreaQuery): Promise<Envelope<DailyData>> {
  checkBox(q.bbox);
  checkRange(q.start, q.end);
  await wait(150);
  const s = series(q.bbox, q.types);
  const days: DailyValue[] = [];
  const missing: string[] = [];
  let total = 0, frp = 0, withData = 0;
  for (const d of eachDay(q.start, q.end)) {
    const v = s.get(d) ?? null;
    const o = originOf(d);
    if (v === null) {
      missing.push(d);
      days.push({ fire_date: d, count: null, count_day: null, count_night: null, frp_sum: null, n_rows: 0, origin_mix: {}, has_data: 'none', uncertainty: null, daynight_reliable: true });
      continue;
    }
    withData++;
    total += v;
    frp += v * 9.3;
    days.push({
      fire_date: d, count: v, count_day: r1(v * 0.547), count_night: r1(v * 0.453), frp_sum: r1(v * 9.3),
      n_rows: Math.round(isModel(o) ? v / 1.08 : v), origin_mix: { [o]: 1 }, has_data: 'full',
      uncertainty: uncertaintyOf(d, o), daynight_reliable: !(o === 'gapfill' && d >= '2022-01-01'),
    });
  }
  return envelope(q.bbox, q.types, { days, totals: { count: r1(total), frp_sum: r1(frp), days_with_data: withData, days_total: days.length } },
    { start: q.start, end: q.end, missing, warnings: warningsFor(q.bbox, q.start, q.end) });
}

export async function calendar(b: BBox, types: number[]): Promise<Envelope<CalendarData>> {
  checkBox(b);
  await wait(250);
  const s = series(b, types);
  const latestY = +LATEST.slice(0, 4), latestM = +LATEST.slice(5, 7);
  const cells: CalendarCell[] = [];
  for (let y = 2000; y <= latestY; y++) {
    for (let m = 1; m <= 12; m++) {
      if (ymd(y, m, 1) < RECORD_START || (y === latestY && m > latestM)) continue;
      const dim = daysInMonth(y, m);
      let sum = 0, withData = 0, unc = 0;
      const byOrigin = new Map<Origin, number>();
      for (let d = 1; d <= dim; d++) {
        const key = ymd(y, m, d);
        if (key > LATEST) break;
        const v = s.get(key);
        if (v === null || v === undefined) continue;
        withData++;
        sum += v;
        const o = originOf(key);
        byOrigin.set(o, (byOrigin.get(o) ?? 0) + v);
        unc += v * uncertaintyOf(key, o);
      }
      const main = [...byOrigin].sort((a, c) => c[1] - a[1])[0]?.[0] ?? null;
      const has: HasData = withData === 0 ? 'none' : withData < dim ? 'partial' : 'full';
      cells.push({
        year: y, month: m, count: withData ? r1(sum) : null, has_data: has, days_with_data: withData, days_total: dim,
        complete: ymd(y, m, dim) <= LATEST, main_origin: main, uncertainty: withData ? +(unc / Math.max(1, sum)).toFixed(3) : null,
      });
    }
  }
  const climatology: Climatology[] = Array.from({ length: 12 }, (_, i) => {
    const xs = cells.filter((c) => c.month === i + 1 && c.complete && c.has_data === 'full').map((c) => c.count!).sort((a, c) => a - c);
    return {
      month: i + 1, median: r1(quantile(xs, 0.5)), p10: r1(quantile(xs, 0.1)), p25: r1(quantile(xs, 0.25)),
      p75: r1(quantile(xs, 0.75)), p90: r1(quantile(xs, 0.9)), min: xs[0] ?? 0, max: xs[xs.length - 1] ?? 0, years_used: xs.length,
    };
  });
  const all = cells.filter((c) => c.count !== null).map((c) => c.count!).sort((a, c) => a - c);
  return envelope(b, types, { cells, climatology, scale: { min: all[0] ?? 0, max: all[all.length - 1] ?? 0, p95: r1(quantile(all, 0.95)) } },
    { query: { start_year: 2000, end_year: latestY }, warnings: warningsFor(b, RECORD_START, LATEST).filter((w) => w.code !== 'RECENT_DATA_PROVISIONAL') });
}

export async function anomaly(b: BBox, month: string, types: number[], baseline?: [number, number]): Promise<Envelope<AnomalyData>> {
  checkBox(b);
  await wait(150);
  const s = series(b, types);
  const y = +month.slice(0, 4), m = +month.slice(5, 7);
  const inProgress = month === LATEST.slice(0, 7);
  const toDay = inProgress ? +LATEST.slice(8, 10) : daysInMonth(y, m);
  const sumFor = (yy: number): { v: number | null; has: HasData } => {
    let sum = 0, n = 0, total = 0;
    for (let d = 1; d <= Math.min(toDay, daysInMonth(yy, m)); d++) {
      const key = ymd(yy, m, d);
      if (key < RECORD_START || key > LATEST) continue;
      total++;
      const v = s.get(key);
      if (v === null || v === undefined) continue;
      n++;
      sum += v;
    }
    return { v: n ? r1(sum) : null, has: n === 0 ? 'none' : n < total || total < toDay ? 'partial' : 'full' };
  };
  const [by0, by1] = baseline ?? [2001, y - 1];
  const history: AnomalyData['history'] = [];
  const base: number[] = [];
  const cur = sumFor(y);
  // contract v1.2: the assessed year appears once in history, even when it is inside the baseline
  for (let yy = Math.min(by0, y); yy <= Math.max(by1, y); yy++) {
    const inBase = yy >= by0 && yy <= by1;
    if (!inBase && yy !== y) continue;
    const r = yy === y ? cur : sumFor(yy);
    history.push({ year: yy, value: r.v, has_data: r.has, main_origin: originOf(ymd(yy, m, 15)) });
    if (inBase && r.has === 'full' && r.v !== null) base.push(r.v);
  }
  const inBaseline = y >= by0 && y <= by1;
  const sorted = [...base].sort((a, c) => a - c);
  const mean = base.reduce((a, c) => a + c, 0) / Math.max(1, base.length);
  const std = Math.sqrt(base.reduce((a, c) => a + (c - mean) ** 2, 0) / Math.max(1, base.length));
  const stats = { p10: r1(quantile(sorted, 0.1)), p25: r1(quantile(sorted, 0.25)), p75: r1(quantile(sorted, 0.75)), p90: r1(quantile(sorted, 0.9)) };
  const median = r1(quantile(sorted, 0.5));
  const v = cur.v;
  const from = ymd(y, m, 1) < RECORD_START ? RECORD_START : ymd(y, m, 1);
  const warnings = warningsFor(b, from, inProgress ? LATEST : ymd(y, m, daysInMonth(y, m)));
  if (inProgress) warnings.unshift({ code: 'MONTH_IN_PROGRESS', message: `Month to date: days 1–${toDay} compared with the same days in other years.` });
  return envelope(b, types, {
    month, compared_days: { from_day: 1, to_day: toDay, month_to_date: inProgress }, value: v,
    baseline: { years: [by0, by1], n_years: base.length, median, mean: r1(mean), std: r1(std), ...stats },
    percent_of_median: v !== null && median ? r1((100 * v) / median) : null,
    z_score: v !== null && std ? +((v - mean) / std).toFixed(2) : null,
    rank: v !== null ? (inBaseline ? base : [...base, v]).sort((a, c) => c - a).indexOf(v) + 1 : null,
    rank_of: inBaseline ? base.length : base.length + 1,
    category: v !== null && base.length ? categoryOf(v, stats) : null,
    has_data: cur.has,
    history,
  }, { query: { month }, warnings });
}

// ---------- map grid ----------
// Rough Australia + Tasmania outline, only used to keep mock fires on land.
const AUS: Array<Array<[number, number]>> = [
  [[113.6,-22],[114.2,-26.3],[115,-30],[115,-33.6],[116.5,-35],[118,-35.1],[123.5,-33.9],[126,-32.3],[129,-31.6],[131.2,-31.5],[134.2,-32.9],[135.6,-34.8],[137.8,-35.6],[138.5,-34.8],[139.6,-36.9],[140.6,-38],[143.5,-38.8],[146.3,-39.1],[148,-37.8],[150,-37.4],[150.9,-34.5],[152.5,-32.4],[153.6,-28.2],[153.1,-25],[150.8,-22.6],[149,-20.4],[146.3,-18.9],[145.4,-16],[145.3,-14.9],[143.6,-13.9],[142.5,-10.7],[141.6,-12.9],[141.5,-15.5],[140.6,-17.5],[139.3,-17.4],[137,-15.9],[135.5,-15],[136.7,-12.3],[136.6,-11.9],[132.6,-11.5],[131,-12.2],[130,-13],[129.5,-14.9],[128.2,-15],[127,-13.8],[125,-14.5],[123.6,-16.3],[122.2,-17.9],[121,-19.5],[118.8,-20.3],[116.7,-20.6],[114.6,-21.8]],
  [[144.6,-40.7],[148.3,-40.9],[148,-43.2],[146.8,-43.6],[145.2,-42.2]],
];
function onLand(lon: number, lat: number): boolean {
  return AUS.some((poly) => {
    let inside = false;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const [xi, yi] = poly[i], [xj, yj] = poly[j];
      if (yi > lat !== yj > lat && lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  });
}

export async function grid(q: AreaQuery): Promise<Envelope<GridData>> {
  checkBox(q.bbox);
  checkRange(q.start, q.end);
  if (daysBetween(q.start, q.end) > LIMITS.grid_max_days)
    throw new ApiError('QUERY_TOO_LARGE', `The map can show at most ${LIMITS.grid_max_days} days at once.`, 400, 'end');
  await wait(200);
  const b = q.bbox;
  const s = series(b, q.types);
  let total = 0;
  const missing: string[] = [];
  const origins = new Map<Origin, number>();
  for (const d of eachDay(q.start, q.end)) {
    const v = s.get(d);
    if (v === null || v === undefined) { missing.push(d); continue; }
    total += v;
    origins.set(originOf(d), (origins.get(originOf(d)) ?? 0) + v);
  }
  const main = [...origins].sort((a, c) => c[1] - a[1])[0]?.[0] ?? 'real';
  const y = +q.start.slice(0, 4), m = +q.start.slice(5, 7); // seasonal weights from the first month
  const rand = rng(`grid-${q.start}-${q.end}-${JSON.stringify(b)}`);
  const weights = BANDS.map((band) => band.peak * band.season[m - 1] * yearEffect(band.id, y, m) * overlapFrac(b, band));
  const wSum = weights.reduce((a, c) => a + c, 0);
  const nClusters = Math.min(450, Math.max(1, Math.round(total / 250)));
  const cells = new Map<string, GridCell>();
  for (let k = 0; k < nClusters && wSum > 0; k++) {
    let pick = rand() * wSum, bi = 0;
    while (pick > weights[bi] && bi < BANDS.length - 1) pick -= weights[bi++];
    const band = BANDS[bi];
    const latLo = Math.max(b.min_lat, band.lat[0]), latHi = Math.min(b.max_lat, band.lat[1]);
    const lonLo = Math.max(b.min_lon, LON[0]), lonHi = Math.min(b.max_lon, LON[1]);
    let lon = 0, lat = 0, ok = false;
    for (let t = 0; t < 40 && !ok; t++) {
      lon = lonLo + rand() * (lonHi - lonLo);
      lat = latLo + rand() * (latHi - latLo);
      ok = onLand(lon, lat);
    }
    if (!ok) continue;
    const size = (total / nClusters) * (0.2 + rand() * rand() * 4);
    const radius = 1 + Math.floor(rand() * 4), parts = 3 + Math.floor(rand() * 14);
    for (let p = 0; p < parts; p++) {
      const cl = Math.floor((lat + (rand() - 0.5) * radius * 0.2) * 10) / 10;
      const cn = Math.floor((lon + (rand() - 0.5) * radius * 0.2) * 10) / 10;
      if (cl < b.min_lat || cl > b.max_lat || cn < b.min_lon || cn > b.max_lon) continue;
      const key = `${cl},${cn}`;
      const n = (size / parts) * (0.3 + rand() * 1.4);
      const c = cells.get(key) ?? { lat: cl, lon: cn, count: 0, frp_sum: 0, main_origin: main };
      c.count += n;
      c.frp_sum += n * (3 + rand() * 12);
      cells.set(key, c);
    }
  }
  const list = [...cells.values()];
  const raw = list.reduce((a, c) => a + c.count, 0) || 1;
  for (const c of list) { c.count = r1((c.count * total) / raw); c.frp_sum = r1((c.frp_sum * total) / raw); }
  return envelope(b, q.types, { res: 0.1, total: r1(total), max_count: Math.max(0, ...list.map((c) => c.count)), cells: list },
    { start: q.start, end: q.end, missing, warnings: warningsFor(b, q.start, q.end) });
}

// ---------- CSV ----------
const CSV_COLS = ['latitude', 'longitude', 'bright_ti4', 'scan', 'track', 'acq_date', 'acq_time', 'satellite', 'instrument', 'confidence', 'version', 'bright_ti5', 'frp', 'daynight', 'type', 'fire_date', 'origin', 'weight'];

export async function hotspotsCsv(q: AreaQuery): Promise<string> {
  if (boxAreaDeg2(q.bbox) * daysBetween(q.start, q.end) > LIMITS.csv_max_deg2_days)
    throw new ApiError('QUERY_TOO_LARGE', 'Too large for one CSV. Choose a smaller area or shorter period.', 400, null, 'Use /api/daily');
  const g = (await grid(q)).data;
  const rand = rng(`rows-${q.start}-${q.end}`);
  const scale = Math.min(1, 5000 / Math.max(1, g.total));
  const lines = [CSV_COLS.join(',')];
  const dayCount = daysBetween(q.start, q.end);
  for (const c of g.cells) {
    const n = Math.max(1, Math.round(c.count * scale));
    for (let i = 0; i < n; i++) {
      const fd = addDays(q.start, Math.floor(rand() * dayCount));
      const o = originOf(fd);
      const day = rand() < 0.55;
      const hh = day ? 3 + Math.floor(rand() * 3) : 14 + Math.floor(rand() * 4);
      const conf = rand();
      lines.push([
        (c.lat + rand() * 0.1).toFixed(5), (c.lon + rand() * 0.1).toFixed(5), (300 + rand() * 67).toFixed(2),
        (0.32 + rand() * 0.3).toFixed(2), (0.36 + rand() * 0.25).toFixed(2), fd,
        `${pad(hh)}${pad(Math.floor(rand() * 60))}`, 'N', 'VIIRS', conf < 0.087 ? 'l' : conf < 0.904 ? 'n' : 'h',
        o === 'ml' ? 'ML-XGB-1.0' : o === 'gapfill' ? 'ML-XGB-1.0-gapfill' : '2', (285 + rand() * 25).toFixed(2),
        (Math.exp(rand() * 4) - 0.5).toFixed(2), day ? 'D' : 'N', q.types[0] ?? 0, fd, o, isModel(o) ? (1.04 + rand() * 0.6).toFixed(2) : '1.0',
      ].join(','));
    }
  }
  return lines.join('\n') + '\n';
}
