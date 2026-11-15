// The AGNI API, answered in the browser from the dataset files (manifest.json + Parquet).
// Same shapes as docs/API_CONTRACT.md v1.2; the logic is a port of backend/app/analytics.py.
//
// Plain JavaScript over hyparquet (no database engine to download): small files are loaded whole
// and kept in memory; large history files are read in parts, only the row groups a query can use.

import type {
  AnomalyData, AreaQuery, BBox, CalendarCell, CalendarData, Category, Climatology, DailyData, DailyValue,
  Envelope, GridCell, GridData, HasData, Meta, Origin, Warning,
} from '../types';
import { fairCategory } from '../types';
import { ApiError } from '../errors';
import { num, readRemote, readWholeFile, text, type Prune, type Row } from './parquet';
import { boxAreaDeg2, daysBetween, daysInMonth, eachDay, minDate, ymd } from '../../lib/dates';

// ---------------------------------------------------------------- dataset

export interface ManifestDay {
  has_data: HasData; source: string | null; flag: string | null; daynight_reliable: boolean;
  detections?: number; processed_at?: string;
}
export interface Manifest {
  version: number; dataset: string; record_start: string; local_end: string; latest_fire_date: string;
  known_gaps: { from: string; to: string; reason: string }[]; periods: Meta['periods'];
  days: Record<string, ManifestDay>; live_months: string[]; updated_at: string;
  /** history detection files, one per year: year -> version (older datasets: one hist/rows.parquet) */
  row_years?: Record<string, string>;
  /** hist/day_totals.parquet exists: all-Australia totals per day (small, loaded whole) */
  totals?: boolean;
}

const BASE = new URL(((import.meta.env.VITE_DATA_URL as string | undefined) || `${import.meta.env.BASE_URL}data/mock`).replace(/\/?$/, '/'), location.href).href;
export const WORKER = ((import.meta.env.VITE_WORKER_URL as string | undefined) ?? '').replace(/\/$/, '');
const fileUrl = (path: string) => new URL(path, BASE).href;

const COVERAGE: BBox = { min_lat: -44, max_lat: -9, min_lon: 112, max_lon: 154 };
const LIMITS: Meta['limits'] = { hotspots_max_limit: 20000, small_box_deg: 0.5, hotspots_max_deg2_days: 4000, csv_max_deg2_days: 20000, grid_max_days: 366 };

/** One 0.1° cell × fire-day × pass × type × origin (hist/cells.parquet, live/cells/*.parquet). */
interface Cell { d: string; p: string; t: number; o: string; ci: number; cj: number; lat: number; lon: number; c: number; f: number; n: number }
/** All-Australia totals per fire-day × pass × type × origin (hist/day_totals.parquet). */
interface Total { d: string; p: string; t: number; o: string; c: number; f: number; n: number }
interface StatusRow { d: string; h: HasData; r: boolean; fl: string | null }

interface Data {
  m: Manifest;
  status: Map<string, StatusRow>; // every fire-day: history + days added later
  statusList: StatusRow[];
  live: Cell[];                   // cells of the days added after the history
  totals: Total[] | null;         // all-Australia totals (history + live), when the dataset has them
}

const CELL_COLUMNS = ['fire_date', 'pass', 'type', 'origin', 'ci', 'cj', 'cell_lat', 'cell_lon', 'count', 'frp_sum', 'n_rows'];
const toCell = (r: Row): Cell => ({
  d: text(r.fire_date)!, p: String(r.pass), t: num(r.type), o: String(r.origin), ci: num(r.ci), cj: num(r.cj),
  lat: num(r.cell_lat), lon: num(r.cell_lon), c: num(r.count), f: num(r.frp_sum), n: num(r.n_rows),
});

let current: Data | null = null;
let setup: Promise<Data> | null = null;

async function fetchManifest(): Promise<Manifest> {
  let res: Response;
  try {
    res = await fetch(fileUrl('manifest.json'), { cache: 'no-store' });
  } catch (e) {
    throw new ApiError('NETWORK', String(e));
  }
  if (!res.ok) throw new ApiError('INTERNAL_ERROR', `Dataset not available (HTTP ${res.status}).`, res.status);
  return res.json();
}

// Small files, parsed, by path; reused across dataset reloads while their version is unchanged.
const small = new Map<string, { version: string; rows: Promise<Row[]> }>();
function smallFile(path: string, version: string, columns?: string[]): Promise<Row[]> {
  const hit = small.get(path);
  if (hit && hit.version === version) return hit.rows;
  const rows = readWholeFile(fileUrl(`${path}?v=${encodeURIComponent(version)}`), columns);
  small.set(path, { version, rows });
  rows.catch(() => { if (small.get(path)?.rows === rows) small.delete(path); }); // download again next time
  return rows;
}

/** A live month's version: changes only when one of its days is (re)processed. */
function monthVersion(m: Manifest, month: string): string {
  let v = '';
  for (const [d, s] of Object.entries(m.days)) if (d.startsWith(month) && (s.processed_at ?? '') > v) v = s.processed_at ?? '';
  return v || m.updated_at;
}

/**
 * Version of the history files, so browsers never reuse a cached copy of an older build. The
 * record's end date isn't enough: a rebuild can end on the same date as an earlier one (new data
 * up to 2025-12-31 after an old build to 2025-12-31), and then the old cached bytes get mixed
 * with the new file's ranges. The newest row-file stamp changes on every history write (full
 * build or consolidate), so it is used when present.
 */
const histVersion = (m: Manifest) =>
  m.row_years ? Object.values(m.row_years).reduce((a, v) => (v > a ? v : a), m.local_end) : m.local_end;
const histUrl = (m: Manifest, name: string) => fileUrl(`hist/${name}.parquet?v=${encodeURIComponent(histVersion(m))}`);

async function load(m: Manifest): Promise<Data> {
  // everything small, in parallel: day coverage, all-Australia totals, the live months' cells
  const [statusRows, totalRows, liveParts] = await Promise.all([
    smallFile('hist/day_status.parquet', histVersion(m), ['fire_date', 'has_data', 'daynight_reliable', 'flag']),
    m.totals ? smallFile('hist/day_totals.parquet', histVersion(m)) : Promise.resolve(null),
    Promise.all(m.live_months.map((mo) => smallFile(`live/cells/${mo}.parquet`, monthVersion(m, mo), CELL_COLUMNS))),
  ]);
  const status = new Map<string, StatusRow>();
  for (const r of statusRows) {
    const d = text(r.fire_date)!;
    status.set(d, { d, h: String(r.has_data) as HasData, r: r.daynight_reliable !== false, fl: text(r.flag) });
  }
  for (const [d, s] of Object.entries(m.days)) status.set(d, { d, h: s.has_data, r: s.daynight_reliable !== false, fl: s.flag });
  const live = liveParts.flat().map(toCell);

  let totals: Total[] | null = null;
  if (totalRows) {
    totals = totalRows.map((r) => ({ d: text(r.fire_date)!, p: String(r.pass), t: num(r.type), o: String(r.origin), c: num(r.count), f: num(r.frp_sum), n: num(r.n_rows) }));
    // the live days, summed over the cells inside the coverage box (as the pipeline does)
    const sums = new Map<string, Total>();
    for (const c of live) {
      if (!inBox(c, COVERAGE)) continue;
      const key = `${c.d}|${c.p}|${c.t}|${c.o}`;
      const s = sums.get(key) ?? { d: c.d, p: c.p, t: c.t, o: c.o, c: 0, f: 0, n: 0 };
      s.c += c.c; s.f += c.f; s.n += c.n;
      sums.set(key, s);
    }
    totals.push(...sums.values());
  }
  const statusList = [...status.values()].sort((a, b) => (a.d < b.d ? -1 : 1));
  return { m, status, statusList, live, totals };
}

/** Load (or reload, after new days were processed) the dataset. */
async function data(refresh = false): Promise<Data> {
  if (!setup || refresh) {
    const previous = setup;
    const attempt = (async () => {
      const m = await fetchManifest();
      if (!current || m.updated_at !== current.m.updated_at) current = await load(m);
      return current;
    })();
    setup = attempt;
    // A failed reload keeps the last good dataset; a failed first load is retried on the next call.
    attempt.catch(() => { if (setup === attempt) setup = previous; });
  }
  return setup;
}

export async function dataset(refresh = false): Promise<Manifest> {
  return (await data(refresh)).m;
}

// ---------------------------------------------------------------- helpers

const today = () => new Date().toISOString().slice(0, 10);

function uncertaintyOf(origin: string, d: string): number {
  if (origin === 'ml') return d < '2002-07-04' ? 0.16 : 0.11;
  if (origin === 'gapfill') return d >= '2022-01-01' ? 0.13 : 0.11;
  return 0;
}

function percentile(values: number[], p: number): number {
  if (!values.length) return 0;
  const s = [...values].sort((a, b) => a - b);
  const pos = (s.length - 1) * p;
  const lo = Math.floor(pos), hi = Math.min(lo + 1, s.length - 1);
  return s[lo] + (s[hi] - s[lo]) * (pos - lo);
}

const inBox = (c: { lat: number; lon: number }, b: BBox) =>
  c.lat >= b.min_lat && c.lat <= b.max_lat && c.lon >= b.min_lon && c.lon <= b.max_lon;

const isWhole = (b: BBox) =>
  b.min_lat <= COVERAGE.min_lat && b.max_lat >= COVERAGE.max_lat && b.min_lon <= COVERAGE.min_lon && b.max_lon >= COVERAGE.max_lon;

/** Row groups of the 0.1° files are sorted by date (cells) or by grid row (cells_month): skip by stats. */
const dateOverlap = (start: string, end: string): Prune => (min, max) => String(min) <= end && String(max) >= start;
const cjRange = (b: BBox): Prune => {
  const lo = Math.floor(b.min_lat * 10) - 1, hi = Math.floor(b.max_lat * 10) + 1;
  return (min, max) => num(min) <= hi && num(max) >= lo;
};

/** History cells between two dates (inclusive), read in parts; plus the live cells. */
async function cellsBetween(dt: Data, start: string, end: string, b: BBox, types: number[]): Promise<Cell[]> {
  const out: Cell[] = [];
  const keep = (c: Cell) => c.d >= start && c.d <= end && types.includes(c.t) && inBox(c, b);
  if (start <= dt.m.local_end) {
    const last = minDate(end, dt.m.local_end);
    const rows = await readRemote(histUrl(dt.m, 'cells'), CELL_COLUMNS, { fire_date: dateOverlap(start, last) });
    for (const r of rows) { const c = toCell(r); if (keep(c)) out.push(c); }
  }
  for (const c of dt.live) if (keep(c)) out.push(c);
  return out;
}

/** Day totals or cells for an area and period: the small totals for the whole coverage, else cells. */
async function dayValues(dt: Data, start: string, end: string, b: BBox, types: number[]): Promise<Total[]> {
  if (dt.totals && isWhole(b)) return dt.totals.filter((x) => x.d >= start && x.d <= end && types.includes(x.t));
  return cellsBetween(dt, start, end, b, types);
}

interface MonthSum { y: number; m: number; o: string; c: number }

/** Totals per year × month × origin for an area, for the months `want(y, m)` selects. */
async function monthSums(dt: Data, b: BBox, types: number[], want: (y: number, m: number) => boolean = () => true): Promise<MonthSum[]> {
  const sums = new Map<string, MonthSum>();
  const add = (y: number, m: number, o: string, c: number) => {
    if (!want(y, m)) return;
    const key = `${y}|${m}|${o}`;
    const s = sums.get(key) ?? { y, m, o, c: 0 };
    s.c += c;
    sums.set(key, s);
  };
  if (dt.totals && isWhole(b)) {
    for (const x of dt.totals) if (types.includes(x.t)) add(+x.d.slice(0, 4), +x.d.slice(5, 7), x.o, x.c);
  } else {
    // history: the monthly cell file, only the row groups covering the box's grid rows
    const rows = await readRemote(histUrl(dt.m, 'cells_month'), ['year', 'month', 'type', 'origin', 'cell_lat', 'cell_lon', 'count', 'cj'], { cj: cjRange(b) });
    for (const r of rows) {
      if (types.includes(num(r.type)) && inBox({ lat: num(r.cell_lat), lon: num(r.cell_lon) }, b))
        add(num(r.year), num(r.month), String(r.origin), num(r.count));
    }
    for (const c of dt.live) if (types.includes(c.t) && inBox(c, b)) add(+c.d.slice(0, 4), +c.d.slice(5, 7), c.o, c.c);
  }
  return [...sums.values()];
}

function checkBox(b: BBox) {
  if (b.min_lat >= b.max_lat || b.min_lon >= b.max_lon) throw new ApiError('EMPTY_BOX', 'Select an area with a nonzero width and height.', 400);
  if (b.min_lat < COVERAGE.min_lat || b.max_lat > COVERAGE.max_lat || b.min_lon < COVERAGE.min_lon || b.max_lon > COVERAGE.max_lon)
    throw new ApiError('BOX_OUTSIDE_COVERAGE', 'The selected area must be inside Australia.', 400);
}

// Notes about the data, one per topic and always in this order: where the numbers come from,
// then days without (complete) data, then how to read them.
const NOTES = {
  model: { code: 'MODEL_DATA_INCLUDED', message: 'Before 2012, counts are estimated from MODIS by Harmonic, AGNI’s harmonization model (VIIRS began in 2012).' },
  gapfill: { code: 'VIIRS_OFFLINE', message: 'On days when no VIIRS satellite was available, Harmonic estimated the detections from MODIS.' },
  snpp: { code: 'SNPP_OFFLINE', message: 'On days when S-NPP was offline, the same VIIRS instrument on NOAA-20 or NOAA-21 was used.' },
  outage: { code: 'KNOWN_OUTAGE', message: 'Some days have no satellite data at all.' },
  low: { code: 'FIRMS_LOW_DELIVERY', message: 'Some recent days have incomplete satellite delivery.' },
  unavailable: { code: 'FIRMS_UNAVAILABLE', message: 'Recent satellite data could not be fetched for some days yet.' },
  daynight: { code: 'DAYNIGHT_UNRELIABLE', message: 'Day and night counts are not split on Harmonic-estimated days from 2022 on.' },
  smallBox: { code: 'SMALL_BOX_MODEL_PERIOD', message: 'Harmonic estimates are reliable as totals, less so for very small areas.' },
  provisional: { code: 'RECENT_DATA_PROVISIONAL', message: 'The last 3 days are near-real-time data and may still change.' },
} satisfies Record<string, Warning>;

/** The order notes are shown in (also used to merge notes from several queries). */
export const NOTE_ORDER = Object.values(NOTES).map((n) => n.code);

function warnings(status: StatusRow[], origins: Set<string>, b: BBox, start: string): Warning[] {
  const flags = new Set(status.map((s) => s.fl).filter(Boolean) as string[]);
  const gapfill = origins.has('gapfill') || flags.has('viirs_offline');
  const model = origins.has('ml') || gapfill;
  const small = b.max_lat - b.min_lat < LIMITS.small_box_deg && b.max_lon - b.min_lon < LIMITS.small_box_deg;
  const out: (Warning | false)[] = [
    origins.has('ml') && NOTES.model,
    gapfill && NOTES.gapfill,
    flags.has('snpp_offline') && NOTES.snpp,
    (flags.has('outage') || status.some((s) => s.h === 'none' && !s.fl)) && NOTES.outage,
    flags.has('firms_low') && NOTES.low,
    flags.has('firms_error') && NOTES.unavailable,
    status.some((s) => !s.r) && NOTES.daynight,
    model && small && (start <= '2011-12-31' || gapfill) && NOTES.smallBox,
    flags.has('provisional') && NOTES.provisional,
  ];
  return out.filter((w): w is Warning => !!w);
}

function envelope<T>(b: BBox, types: number[], data: T, extra: { start?: string; end?: string; missing?: string[]; warnings?: Warning[]; query?: object } = {}): Envelope<T> {
  return {
    query: { bbox: b, start: extra.start, end: extra.end, types, ...extra.query },
    partial: (extra.missing ?? []).length > 0,
    missing_dates: extra.missing ?? [],
    warnings: extra.warnings ?? [],
    generated_at: new Date().toISOString().slice(0, 19) + 'Z',
    data,
  };
}

const statusBetween = (dt: Data, start: string, end: string) => dt.statusList.filter((s) => s.d >= start && s.d <= end);

// ---------------------------------------------------------------- endpoints

export async function meta(): Promise<Meta> {
  const m = await dataset();
  const liveStart = new Date(Date.parse(`${m.local_end}T00:00:00Z`) + 86_400_000).toISOString().slice(0, 10);
  return {
    coverage: { record_start: m.record_start, local_end: m.local_end, latest_fire_date: m.latest_fire_date, bbox: COVERAGE },
    periods: [
      ...m.periods.map((p) => (p.to === null ? { ...p, to: m.local_end } : p)),
      { from: liveStart, to: null, origin: 'firms_nrt', label: 'VIIRS via NASA FIRMS (S-NPP, or NOAA-20/21 when it is offline; Harmonic from MODIS when no VIIRS is available)', uncertainty: 0 },
    ],
    known_gaps: m.known_gaps,
    types: [{ value: 0, label: 'Vegetation fire' }, { value: 2, label: 'Static / industrial source' }, { value: 3, label: 'Offshore' }],
    limits: LIMITS,
    firms: { status: 'ok', last_fetch: m.updated_at },
    contract_version: '1.2',
    dataset: m.dataset,
  };
}

export async function daily(q: AreaQuery): Promise<Envelope<DailyData>> {
  checkBox(q.bbox);
  const dt = await data();
  const rows = await dayValues(dt, q.start, q.end, q.bbox, q.types);
  const status = statusBetween(dt, q.start, q.end);
  const byDay = new Map<string, Total[]>();
  for (const r of rows) {
    const list = byDay.get(r.d);
    if (list) list.push(r); else byDay.set(r.d, [r]);
  }
  const days: DailyValue[] = [];
  const missing: string[] = [];
  for (const d of eachDay(q.start, q.end)) {
    const s = dt.status.get(d);
    const coverage: HasData = s?.h ?? 'none';
    const values = byDay.get(d) ?? [];
    const weights: Record<string, number> = {};
    for (const v of values) weights[v.o] = (weights[v.o] ?? 0) + v.c;
    const count = values.reduce((a, v) => a + v.c, 0);
    const available = coverage !== 'none';
    if (coverage !== 'full') missing.push(d);
    days.push({
      fire_date: d,
      count: available ? count : null,
      count_day: available ? values.filter((v) => v.p === 'D').reduce((a, v) => a + v.c, 0) : null,
      count_night: available ? values.filter((v) => v.p === 'N').reduce((a, v) => a + v.c, 0) : null,
      frp_sum: available ? values.reduce((a, v) => a + v.f, 0) : null,
      n_rows: values.reduce((a, v) => a + v.n, 0),
      origin_mix: count ? Object.fromEntries(Object.entries(weights).map(([o, w]) => [o, w / count])) : {},
      has_data: coverage,
      uncertainty: available ? (count ? Object.entries(weights).reduce((a, [o, w]) => a + uncertaintyOf(o, d) * w, 0) / count : 0) : null,
      daynight_reliable: s?.r ?? true,
    });
  }
  const totals = {
    count: days.reduce((a, x) => a + (x.count ?? 0), 0),
    frp_sum: days.reduce((a, x) => a + (x.frp_sum ?? 0), 0),
    days_with_data: days.filter((x) => x.count !== null).length,
    days_total: days.length,
  };
  return envelope(q.bbox, q.types, { days, totals }, {
    start: q.start, end: q.end, missing, warnings: warnings(status, new Set(rows.map((r) => r.o)), q.bbox, q.start),
  });
}

export async function grid(q: AreaQuery): Promise<Envelope<GridData>> {
  checkBox(q.bbox);
  if (daysBetween(q.start, q.end) > LIMITS.grid_max_days)
    throw new ApiError('QUERY_TOO_LARGE', `The map can show at most ${LIMITS.grid_max_days} days at once.`, 400);
  const dt = await data();
  const rows = await cellsBetween(dt, q.start, q.end, q.bbox, q.types);
  const status = statusBetween(dt, q.start, q.end);
  const cells = new Map<number, { ci: number; cj: number; w: Record<string, number>; f: number }>();
  for (const r of rows) {
    const key = r.ci * 10_000 + r.cj;
    let cell = cells.get(key);
    if (!cell) { cell = { ci: r.ci, cj: r.cj, w: {}, f: 0 }; cells.set(key, cell); }
    cell.w[r.o] = (cell.w[r.o] ?? 0) + r.c;
    cell.f += r.f;
  }
  const out: GridCell[] = [];
  for (const c of cells.values()) {
    const count = Object.values(c.w).reduce((a, x) => a + x, 0);
    if (count <= 0) continue;
    const main = Object.entries(c.w).sort((a, b) => b[1] - a[1])[0][0] as Origin;
    out.push({ lat: c.cj / 10, lon: c.ci / 10, count, frp_sum: c.f, main_origin: main });
  }
  const missing = [...eachDay(q.start, q.end)].filter((d) => dt.status.get(d)?.h !== 'full');
  return envelope(q.bbox, q.types, {
    res: 0.1, total: out.reduce((a, c) => a + c.count, 0), max_count: out.reduce((a, c) => Math.max(a, c.count), 0), cells: out,
  }, { start: q.start, end: q.end, missing, warnings: warnings(status, new Set(rows.map((r) => r.o)), q.bbox, q.start) });
}

interface MonthStatus { obs: number; allfull: boolean }

/** Days with data per year-month (optionally only up to a day of the month). */
function monthStatus(dt: Data, want: (y: number, m: number) => boolean, toDay = 31): Map<string, MonthStatus> {
  const out = new Map<string, MonthStatus>();
  for (const s of dt.statusList) {
    const y = +s.d.slice(0, 4), m = +s.d.slice(5, 7);
    if (+s.d.slice(8, 10) > toDay || !want(y, m) || s.h === 'none') continue;
    const key = `${y}-${m}`;
    const st = out.get(key) ?? { obs: 0, allfull: true };
    st.obs += 1;
    st.allfull &&= s.h === 'full';
    out.set(key, st);
  }
  return out;
}

function summary(sums: MonthSum[], st: MonthStatus | undefined, expected: number, monthUncertainty: (o: string) => number) {
  const obs = st?.obs ?? 0;
  if (!obs) return { count: null, has_data: 'none' as HasData, days_with_data: 0, main_origin: null, uncertainty: null };
  const weights: Record<string, number> = {};
  for (const s of sums) weights[s.o] = (weights[s.o] ?? 0) + s.c;
  const count = Object.values(weights).reduce((a, x) => a + x, 0);
  const main = Object.keys(weights).length ? (Object.entries(weights).sort((a, b) => b[1] - a[1])[0][0] as Origin) : null;
  return {
    count,
    has_data: (obs === expected && st?.allfull ? 'full' : 'partial') as HasData,
    days_with_data: obs,
    main_origin: main,
    uncertainty: count ? Object.entries(weights).reduce((a, [o, w]) => a + monthUncertainty(o) * w, 0) / count : 0,
  };
}

export async function calendar(b: BBox, types: number[]): Promise<Envelope<CalendarData>> {
  checkBox(b);
  const dt = await data();
  const m = dt.m;
  const sums = await monthSums(dt, b, types);
  const status = monthStatus(dt, () => true);
  const t = today();
  const [ty, tm] = [+t.slice(0, 4), +t.slice(5, 7)];
  const [sy, sm] = [+m.record_start.slice(0, 4), +m.record_start.slice(5, 7)];
  const sumsBy = new Map<string, MonthSum[]>();
  for (const s of sums) sumsBy.set(`${s.y}-${s.m}`, [...(sumsBy.get(`${s.y}-${s.m}`) ?? []), s]);
  const cells: CalendarCell[] = [];
  for (let y = sy; y <= ty; y++) {
    for (let mo = y === sy ? sm : 1; mo <= (y === ty ? tm : 12); mo++) {
      const total = daysInMonth(y, mo);
      const mid = ymd(y, mo, 15);
      const s = summary(sumsBy.get(`${y}-${mo}`) ?? [], status.get(`${y}-${mo}`), total, (o) => uncertaintyOf(o, mid));
      cells.push({ year: y, month: mo, days_total: total, complete: y * 12 + mo < ty * 12 + tm, ...s });
    }
  }
  const seasonal: number[][] = Array.from({ length: 12 }, () => []);
  for (const c of cells) if (c.complete && c.has_data === 'full' && c.count !== null) seasonal[c.month - 1].push(c.count);
  const climatology: Climatology[] = seasonal.map((v, i) => ({
    month: i + 1, median: percentile(v, 0.5), p10: percentile(v, 0.1), p25: percentile(v, 0.25),
    p75: percentile(v, 0.75), p90: percentile(v, 0.9), min: v.length ? Math.min(...v) : 0, max: v.length ? Math.max(...v) : 0, years_used: v.length,
  }));
  const counts = cells.filter((c) => c.count !== null).map((c) => c.count!);
  return envelope(b, types, { cells, climatology, scale: { min: 0, max: counts.reduce((a, x) => Math.max(a, x), 0), p95: percentile(counts, 0.95) } }, {
    query: { start_year: sy, end_year: ty },
    warnings: warnings(dt.statusList.filter((s) => s.fl !== 'provisional'), new Set(sums.map((s) => s.o)), b, m.record_start),
  });
}

function categoryOf(v: number, s: { p10: number; p25: number; p75: number; p90: number }): Category {
  if (v < s.p10) return 'well_below';
  if (v < s.p25) return 'below';
  if (v <= s.p75) return 'normal';
  if (v <= s.p90) return 'above';
  return 'well_above';
}

/**
 * Rank among every year that has data, the same bars the "in every year" chart draws. Years
 * with some days missing count too: missing days only lower a total, so a year that is still
 * higher really is higher (leaving them out made "6th" disagree with the bars).
 */
function rankOf(history: { year: number; value: number | null }[], y: number, value: number | null, inRange: (h: { year: number }) => boolean) {
  if (value === null) return { rank: null, rank_of: history.filter((h) => h.value !== null && inRange(h)).length };
  const others = history.filter((h) => h.year !== y && h.value !== null && inRange(h));
  return { rank: 1 + others.filter((h) => h.value! > value).length, rank_of: others.length + 1 };
}

export async function anomaly(b: BBox, month: string, types: number[], baseline?: [number, number]): Promise<Envelope<AnomalyData>> {
  checkBox(b);
  const dt = await data();
  const m = dt.m;
  const y = +month.slice(0, 4), mo = +month.slice(5, 7);
  const latest = m.latest_fire_date;
  const latestDay = +latest.slice(8, 10);
  const mtd = month === latest.slice(0, 7) && latestDay < daysInMonth(y, mo);
  const toDay = mtd ? latestDay : daysInMonth(y, mo);
  const [first, last] = baseline ?? [+m.record_start.slice(0, 4), y - 1];
  if (baseline && !(first <= last && last <= +latest.slice(0, 4)))
    throw new ApiError('INVALID_DATE_RANGE', 'Choose baseline years inside the record.', 400);
  const years = [...new Set([...Array.from({ length: Math.max(0, last - first + 1) }, (_, i) => first + i), y])]
    .filter((yy) => ymd(yy, mo, daysInMonth(yy, mo)) >= m.record_start && ymd(yy, mo, 1) <= latest)
    .sort((a, c) => a - c);
  const yearSet = new Set(years.length ? years : [y]);
  const want = (yy: number, mm: number) => mm === mo && yearSet.has(yy);

  let sums: MonthSum[];
  if (mtd) {
    // month to date: the same days 1..toDay in every year, from the day values
    const ranges = [...yearSet].map((yy) => [ymd(yy, mo, 1), ymd(yy, mo, Math.min(toDay, daysInMonth(yy, mo)))] as const);
    const parts = await Promise.all(ranges.map(([s, e]) => dayValues(dt, s, e, b, types)));
    const by = new Map<string, MonthSum>();
    for (const x of parts.flat()) {
      const key = `${x.d.slice(0, 4)}|${x.o}`;
      const s = by.get(key) ?? { y: +x.d.slice(0, 4), m: mo, o: x.o, c: 0 };
      s.c += x.c;
      by.set(key, s);
    }
    sums = [...by.values()];
  } else {
    sums = await monthSums(dt, b, types, want);
  }
  const status = monthStatus(dt, want, toDay);
  const history = years.map((yy) => {
    const expected = Math.min(daysInMonth(yy, mo), toDay);
    const s = summary(sums.filter((x) => x.y === yy), status.get(`${yy}-${mo}`), expected, (o) => uncertaintyOf(o, ymd(yy, mo, 15)));
    return { year: yy, value: s.count, has_data: s.has_data, main_origin: s.main_origin };
  });
  const cur = history.find((h) => h.year === y) ?? { year: y, value: null, has_data: 'none' as HasData, main_origin: null };
  const values = history.filter((h) => h.has_data === 'full' && h.year >= first && h.year <= last).map((h) => h.value!);
  const value = cur.value;
  const mean = values.length ? values.reduce((a, x) => a + x, 0) / values.length : 0;
  const std = values.length ? Math.sqrt(values.reduce((a, x) => a + (x - mean) ** 2, 0) / values.length) : 0;
  const stats = { p10: percentile(values, 0.1), p25: percentile(values, 0.25), p75: percentile(values, 0.75), p90: percentile(values, 0.9) };
  const median = percentile(values, 0.5);
  const st = statusBetween(dt, ymd(y, mo, 1), ymd(y, mo, Math.min(toDay, daysInMonth(y, mo))));
  const w = warnings(st, new Set(sums.filter((x) => x.y === y).map((x) => x.o)), b, ymd(y, mo, 1));
  if (mtd) w.unshift({ code: 'MONTH_IN_PROGRESS', message: `Month to date: days 1–${toDay} compared with the same days in other years.` });
  return envelope(b, types, {
    month, compared_days: { from_day: 1, to_day: toDay, month_to_date: mtd }, value,
    baseline: { years: [first, last], n_years: values.length, median, mean, std, ...stats },
    percent_of_median: value !== null && median ? (100 * value) / median : null,
    z_score: value !== null && std ? (value - mean) / std : null,
    ...rankOf(history, y, value, (h) => h.year >= first && h.year <= last),
    category: fairCategory(value !== null && values.length ? categoryOf(value, stats) : null, cur.has_data),
    has_data: cur.has_data,
    history,
  }, { query: { month, baseline_start_year: first, baseline_end_year: last }, warnings: w });
}

/**
 * One day compared with the same date in every other year of the record (e.g. 14 December in
 * 2001…2025), with the same statistics as the month comparison. `month` in the result is the
 * date ("YYYY-MM-DD"); compared_days is that single day.
 */
export async function dayAnomaly(b: BBox, date: string, types: number[]): Promise<Envelope<AnomalyData>> {
  checkBox(b);
  const dt = await data();
  const m = dt.m;
  const y = +date.slice(0, 4), mo = +date.slice(5, 7), d = +date.slice(8, 10);
  const latest = m.latest_fire_date;
  // every year that has this date inside the record (29 February only in leap years)
  const years: number[] = [];
  for (let yy = +m.record_start.slice(0, 4); yy <= +latest.slice(0, 4); yy++) {
    const dd = ymd(yy, mo, d);
    if (d <= daysInMonth(yy, mo) && dd >= m.record_start && dd <= latest) years.push(yy);
  }
  const parts = await Promise.all(years.map((yy) => dayValues(dt, ymd(yy, mo, d), ymd(yy, mo, d), b, types)));
  const history = years.map((yy, i) => {
    const st = dt.status.get(ymd(yy, mo, d));
    const has: HasData = st?.h ?? 'none';
    const weights: Record<string, number> = {};
    for (const x of parts[i]) weights[x.o] = (weights[x.o] ?? 0) + x.c;
    const value = has === 'none' ? null : Object.values(weights).reduce((a, x) => a + x, 0);
    const main = Object.keys(weights).length ? (Object.entries(weights).sort((p, q) => q[1] - p[1])[0][0] as Origin) : null;
    return { year: yy, value, has_data: has, main_origin: main };
  });
  const cur = history.find((h) => h.year === y) ?? { year: y, value: null, has_data: 'none' as HasData, main_origin: null };
  const values = history.filter((h) => h.has_data === 'full').map((h) => h.value!);
  const value = cur.value;
  const mean = values.length ? values.reduce((a, x) => a + x, 0) / values.length : 0;
  const std = values.length ? Math.sqrt(values.reduce((a, x) => a + (x - mean) ** 2, 0) / values.length) : 0;
  const stats = { p10: percentile(values, 0.1), p25: percentile(values, 0.25), p75: percentile(values, 0.75), p90: percentile(values, 0.9) };
  const median = percentile(values, 0.5);
  const first = years[0] ?? y, last = years[years.length - 1] ?? y;
  const st = statusBetween(dt, date, date);
  return envelope(b, types, {
    month: date, compared_days: { from_day: d, to_day: d, month_to_date: false }, value,
    baseline: { years: [first, last], n_years: values.length, median, mean, std, ...stats },
    percent_of_median: value !== null && median ? (100 * value) / median : null,
    z_score: value !== null && std ? (value - mean) / std : null,
    ...rankOf(history, y, value, () => true),
    // a single day needs a few comparable years before "usual" means anything
    category: fairCategory(value !== null && values.length >= 5 ? categoryOf(value, stats) : null, cur.has_data),
    has_data: cur.has_data,
    history,
  }, { start: date, end: date, query: { date }, warnings: warnings(st, new Set(parts[years.indexOf(y)]?.map((x) => x.o) ?? []), b, date) });
}

const CSV_COLUMNS = ['latitude', 'longitude', 'bright_ti4', 'scan', 'track', 'acq_date', 'acq_time', 'satellite', 'instrument',
  'confidence', 'version', 'bright_ti5', 'frp', 'daynight', 'type', 'fire_date', 'origin', 'weight'];

/** History detection files for a period: one per year (manifest.row_years), or the older single file. */
function histRowFiles(m: Manifest, start: string, end: string): string[] {
  if (start > m.local_end) return [];
  if (!m.row_years) return [fileUrl(`hist/rows.parquet?v=${encodeURIComponent(m.local_end)}`)];
  const last = minDate(end, m.local_end).slice(0, 4);
  return Object.entries(m.row_years)
    .filter(([year]) => year >= start.slice(0, 4) && year <= last)
    .map(([year, v]) => fileUrl(`hist/rows/${year}.parquet?v=${encodeURIComponent(v)}`));
}

const csvValue = (v: unknown): string => {
  if (v === null || v === undefined) return '';
  const s = typeof v === 'number' || typeof v === 'bigint' ? String(v) : text(v)!;
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};

export async function hotspotsCsv(q: AreaQuery): Promise<Uint8Array> {
  checkBox(q.bbox);
  if (boxAreaDeg2(q.bbox) * daysBetween(q.start, q.end) > LIMITS.csv_max_deg2_days)
    throw new ApiError('QUERY_TOO_LARGE', 'Too large for one CSV. Choose a smaller area or shorter period.', 400);
  const m = await dataset();
  // detection files are only opened here, and only the ones (and row groups) the period touches
  const months = m.live_months.filter((mo) => mo >= q.start.slice(0, 7) && mo <= q.end.slice(0, 7));
  const hist = histRowFiles(m, q.start, q.end);
  if (!hist.length && !months.length) throw new ApiError('NOT_FOUND', 'No detections are stored for this period yet.', 404);
  const parts = await Promise.all([
    ...hist.map((url) => readRemote(url, CSV_COLUMNS, { fire_date: dateOverlap(q.start, q.end) })),
    ...months.map((mo) => readWholeFile(fileUrl(`live/rows/${mo}.parquet?v=${encodeURIComponent(monthVersion(m, mo))}`), CSV_COLUMNS)),
  ]);
  const b = q.bbox;
  const rows = parts.flat().filter((r) => {
    const d = text(r.fire_date)!, lat = num(r.latitude), lon = num(r.longitude);
    return d >= q.start && d <= q.end && lat >= b.min_lat && lat <= b.max_lat && lon >= b.min_lon && lon <= b.max_lon && q.types.includes(num(r.type));
  });
  const key = (r: Row) => `${text(r.acq_date)} ${text(r.acq_time)}`;
  rows.sort((x, y) => (key(x) < key(y) ? -1 : key(x) > key(y) ? 1 : 0));
  const lines = [CSV_COLUMNS.join(','), ...rows.map((r) => CSV_COLUMNS.map((c) => csvValue(r[c])).join(','))];
  return new TextEncoder().encode(lines.join('\n') + '\n');
}

// ---------------------------------------------------------------- live processing (Worker)

export interface EnsureResult { ready: string[]; pending: string[]; started: string[]; error?: string }

/** Ask the Worker to process stored-later days in [start, end]. No-op when no Worker is configured. */
export async function ensureDays(start: string, end: string): Promise<EnsureResult | null> {
  if (!WORKER) return null;
  const m = await dataset();
  const res = await fetch(`${WORKER}/ensure`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dataset: m.dataset, start, end }),
  });
  return res.json();
}

export async function pendingDays(): Promise<string[]> {
  if (!WORKER) return [];
  const m = await dataset();
  const res = await fetch(`${WORKER}/pending/${m.dataset}`);
  return res.ok ? ((await res.json()) as { pending: string[] }).pending : [];
}
