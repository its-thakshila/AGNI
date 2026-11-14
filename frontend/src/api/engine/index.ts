// The AGNI API, answered in the browser from the dataset files (manifest.json + Parquet).
// Same shapes as docs/API_CONTRACT.md v1.2; the logic is a port of backend/app/analytics.py.

import type {
  AnomalyData, AreaQuery, BBox, CalendarCell, CalendarData, Category, Climatology, DailyData, DailyValue,
  Envelope, GridCell, GridData, HasData, Meta, Origin, Warning,
} from '../types';
import { ApiError } from '../errors';
import { connection, day, exportFile, ints, num, query, registerBuffer, dropBuffer, str } from './db';
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
}

const BASE = new URL(((import.meta.env.VITE_DATA_URL as string | undefined) || `${import.meta.env.BASE_URL}data/mock`).replace(/\/?$/, '/'), location.href).href;
export const WORKER = ((import.meta.env.VITE_WORKER_URL as string | undefined) ?? '').replace(/\/$/, '');
const fileUrl = (path: string) => new URL(path, BASE).href;

const COVERAGE: BBox = { min_lat: -44, max_lat: -9, min_lon: 112, max_lon: 154 };
const LIMITS: Meta['limits'] = { hotspots_max_limit: 20000, small_box_deg: 0.5, hotspots_max_deg2_days: 4000, csv_max_deg2_days: 20000, grid_max_days: 366 };

let manifest: Manifest | null = null;
let setup: Promise<Manifest> | null = null;

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

// Small files are downloaded whole, all at once, and kept in memory: DuckDB reads remote files one
// request at a time (~0.3 s each through the Worker), so a dozen small monthly files cost seconds
// per query when read remotely, but a fraction of a second when fetched in parallel up front.
// Only the large history files (cells, cells_month, rows) are read remotely with Range requests.
//
// path -> in-memory file name holding its current version. Each version gets its own name:
// DuckDB keeps details of a registered file, so replacing the bytes under the same name breaks it.
const memFiles = new Map<string, string>();

async function fetchBytes(path: string): Promise<Uint8Array> {
  let res: Response;
  try {
    res = await fetch(fileUrl(path));
  } catch (e) {
    throw new ApiError('NETWORK', String(e));
  }
  if (!res.ok) throw new ApiError('INTERNAL_ERROR', `Dataset file missing: ${path} (HTTP ${res.status}).`, res.status);
  const bytes = new Uint8Array(await res.arrayBuffer());
  // a Parquet file starts and ends with "PAR1"; anything else (an error page, a cut-off download)
  // must not be kept, or every later load would reuse it
  const magic = (at: number) => String.fromCharCode(...bytes.subarray(at, at + 4)) === 'PAR1';
  if (path.split('?')[0].endsWith('.parquet') && (bytes.length < 12 || !magic(0) || !magic(bytes.length - 4)))
    throw new ApiError('INTERNAL_ERROR', `Dataset file is damaged or incomplete: ${path}.`);
  return bytes;
}

/** Download the files whose version changed (in parallel) and register them in memory. */
async function loadSmallFiles(files: { path: string; version: string }[]): Promise<string[]> {
  const loaded: string[] = [];
  await Promise.all(files.map(async ({ path, version }) => {
    const name = `mem/${version.replace(/[^\w.-]/g, '_')}/${path}`;
    const old = memFiles.get(path);
    if (old === name) return;
    const sep = path.includes('?') ? '&' : '?';
    await registerBuffer(name, await fetchBytes(`${path}${sep}v=${encodeURIComponent(version)}`));
    memFiles.set(path, name);
    loaded.push(path);
    if (old) void dropBuffer(old); // the tables built from it are replaced right after
  }));
  return loaded;
}

/** A live month's version: changes only when one of its days is (re)processed. */
function monthVersion(m: Manifest, month: string): string {
  let v = '';
  for (const [d, s] of Object.entries(m.days)) if (d.startsWith(month) && (s.processed_at ?? '') > v) v = s.processed_at ?? '';
  return v || m.updated_at;
}

async function buildViews(m: Manifest) {
  // history files only change when days are consolidated (local_end moves): new URL then, so neither the
  // browser nor DuckDB's footer cache can mix an old file with a new one
  const hv = encodeURIComponent(m.local_end);
  const hist = (name: string) => str(fileUrl(`hist/${name}.parquet?v=${hv}`));
  const versions = new Map(m.live_months.map((mo) => [mo, monthVersion(m, mo)]));
  const liveCells = m.live_months.map((mo) => `live/cells/${mo}.parquet`);
  const loaded = await loadSmallFiles([
    { path: 'hist/day_status.parquet', version: m.local_end },
    ...liveCells.map((path, i) => ({ path, version: versions.get(m.live_months[i])! })),
  ]);
  const mem = (paths: string[]) => `read_parquet([${paths.map((x) => str(memFiles.get(x)!)).join(', ')}])`;
  const stmts = [
    `CREATE OR REPLACE VIEW hist_cells AS SELECT * FROM read_parquet(${hist('cells')})`,
    `CREATE OR REPLACE VIEW hist_cells_month AS SELECT * FROM read_parquet(${hist('cells_month')})`,
    `CREATE OR REPLACE TABLE hist_status AS SELECT fire_date, has_data, daynight_reliable, flag FROM ${mem(['hist/day_status.parquet'])}`,
    liveCells.length
      ? `CREATE OR REPLACE TABLE live_cells AS SELECT * FROM ${mem(liveCells)}`
      : `CREATE OR REPLACE VIEW live_cells AS SELECT * FROM hist_cells WHERE false`,
    `CREATE OR REPLACE TABLE live_status (fire_date DATE, has_data VARCHAR, daynight_reliable BOOLEAN, flag VARCHAR)`,
  ];
  const days = Object.entries(m.days);
  if (days.length) {
    stmts.push(`INSERT INTO live_status VALUES ${days.map(([d, s]) =>
      `(${day(d)}, ${str(s.has_data)}, ${s.daynight_reliable ? 'true' : 'false'}, ${s.flag ? str(s.flag) : 'NULL'})`).join(', ')}`);
  }
  stmts.push(
    `CREATE OR REPLACE VIEW all_cells AS SELECT * FROM hist_cells UNION ALL BY NAME SELECT * FROM live_cells`,
    `CREATE OR REPLACE VIEW all_status AS SELECT * FROM hist_status UNION ALL BY NAME SELECT * FROM live_status`,
  );
  try {
    for (const s of stmts) await query(s);
  } catch (e) {
    for (const path of loaded) memFiles.delete(path); // download them again on the next attempt
    throw e;
  }
}

/** Load (or reload, after new days were processed) the dataset. */
export function dataset(refresh = false): Promise<Manifest> {
  if (!setup || refresh) {
    const previous = setup;
    const attempt = (async () => {
      // start the SQL engine while the manifest downloads
      const [m] = await Promise.all([fetchManifest(), connection()]);
      if (!manifest || m.updated_at !== manifest.updated_at) await buildViews(m);
      manifest = m;
      return m;
    })();
    setup = attempt;
    // A failed reload keeps the last good dataset; a failed first load is retried on the next call
    // (before, the failed promise stayed cached and every later query failed with it).
    attempt.catch(() => { if (setup === attempt) setup = previous; });
  }
  return setup;
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

const boxSql = (b: BBox, types: number[], lat = 'cell_lat', lon = 'cell_lon') =>
  `${lat} BETWEEN ${num(b.min_lat)} AND ${num(b.max_lat)} AND ${lon} BETWEEN ${num(b.min_lon)} AND ${num(b.max_lon)} AND type IN ${ints(types)}`;

function checkBox(b: BBox) {
  if (b.min_lat >= b.max_lat || b.min_lon >= b.max_lon) throw new ApiError('EMPTY_BOX', 'Select an area with a nonzero width and height.', 400);
  if (b.min_lat < COVERAGE.min_lat || b.max_lat > COVERAGE.max_lat || b.min_lon < COVERAGE.min_lon || b.max_lon > COVERAGE.max_lon)
    throw new ApiError('BOX_OUTSIDE_COVERAGE', 'The selected area must be inside Australia.', 400);
}

interface StatusRow { d: string; h: HasData; r: boolean; fl: string | null }

const FLAG_WARNINGS: Record<string, Warning> = {
  outage: { code: 'KNOWN_OUTAGE', message: 'Some days have no satellite data at all (satellite outage).' },
  firms_low: { code: 'FIRMS_LOW_DELIVERY', message: 'Some recent days have incomplete satellite delivery.' },
  firms_error: { code: 'FIRMS_UNAVAILABLE', message: 'Recent satellite observations could not be fetched for some days.' },
  provisional: { code: 'RECENT_DATA_PROVISIONAL', message: 'The last 3 days are near-real-time data and may still change.' },
  viirs_offline: { code: 'VIIRS_OFFLINE', message: 'VIIRS was offline on some days; Harmonic estimated them from MODIS.' },
};

function warnings(status: StatusRow[], origins: Set<string>, b: BBox, start: string): Warning[] {
  const out: Warning[] = [];
  const flags = new Set(status.map((s) => s.fl).filter(Boolean) as string[]);
  for (const [flag, w] of Object.entries(FLAG_WARNINGS)) if (flags.has(flag)) out.push(w);
  if (status.some((s) => s.h === 'none' && !s.fl)) out.push(FLAG_WARNINGS.outage);
  const model = origins.has('ml') || origins.has('gapfill');
  if (model) out.push({ code: 'MODEL_DATA_INCLUDED', message: 'Part of this period is harmonized from MODIS by Harmonic, AGNI’s harmonization model.' });
  if (model && b.max_lat - b.min_lat < LIMITS.small_box_deg && b.max_lon - b.min_lon < LIMITS.small_box_deg && (start <= '2011-12-31' || origins.has('gapfill')))
    out.push({ code: 'SMALL_BOX_MODEL_PERIOD', message: 'Model-based years are reliable only as totals for small areas.' });
  if (status.some((s) => !s.r)) out.push({ code: 'DAYNIGHT_UNRELIABLE', message: 'Day/night split is not shown on gap-filled days.' });
  const seen = new Set<string>();
  return out.filter((w) => !seen.has(w.code) && seen.add(w.code));
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

async function statusRows(where: string): Promise<StatusRow[]> {
  return query<StatusRow>(`SELECT strftime(fire_date, '%Y-%m-%d') AS d, has_data AS h, COALESCE(daynight_reliable, true) AS r, flag AS fl
                           FROM all_status WHERE ${where}`);
}

// ---------------------------------------------------------------- endpoints

export async function meta(): Promise<Meta> {
  const m = await dataset();
  const liveStart = new Date(Date.parse(`${m.local_end}T00:00:00Z`) + 86_400_000).toISOString().slice(0, 10);
  return {
    coverage: { record_start: m.record_start, local_end: m.local_end, latest_fire_date: m.latest_fire_date, bbox: COVERAGE },
    periods: [
      ...m.periods.map((p) => (p.to === null ? { ...p, to: m.local_end } : p)),
      { from: liveStart, to: null, origin: 'firms_nrt', label: 'VIIRS S-NPP via NASA FIRMS (Harmonic from MODIS when VIIRS is offline)', uncertainty: 0 },
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
  await dataset();
  const range = `fire_date BETWEEN ${day(q.start)} AND ${day(q.end)}`;
  const [rows, status] = await Promise.all([
    query<{ d: string; p: string; o: string; c: number; f: number; n: number }>(
      `SELECT strftime(fire_date, '%Y-%m-%d') AS d, "pass" AS p, origin AS o, SUM(count)::DOUBLE AS c, SUM(frp_sum)::DOUBLE AS f, SUM(n_rows)::DOUBLE AS n
       FROM all_cells WHERE ${range} AND ${boxSql(q.bbox, q.types)} GROUP BY ALL`),
    statusRows(range),
  ]);
  const byDay = new Map<string, typeof rows>();
  for (const r of rows) byDay.set(r.d, [...(byDay.get(r.d) ?? []), r]);
  const st = new Map(status.map((s) => [s.d, s]));
  const days: DailyValue[] = [];
  const missing: string[] = [];
  for (const d of eachDay(q.start, q.end)) {
    const s = st.get(d);
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
  await dataset();
  const range = `fire_date BETWEEN ${day(q.start)} AND ${day(q.end)}`;
  const [rows, status] = await Promise.all([
    query<{ ci: number; cj: number; o: string; c: number; f: number }>(
      `SELECT ci::INT AS ci, cj::INT AS cj, origin AS o, SUM(count)::DOUBLE AS c, SUM(frp_sum)::DOUBLE AS f
       FROM all_cells WHERE ${range} AND ${boxSql(q.bbox, q.types)} GROUP BY ALL`),
    statusRows(range),
  ]);
  const cells = new Map<string, { ci: number; cj: number; w: Record<string, number>; f: number }>();
  for (const r of rows) {
    const key = `${r.ci},${r.cj}`;
    const cell = cells.get(key) ?? { ci: r.ci, cj: r.cj, w: {}, f: 0 };
    cell.w[r.o] = (cell.w[r.o] ?? 0) + r.c;
    cell.f += r.f;
    cells.set(key, cell);
  }
  const out: GridCell[] = [];
  for (const c of cells.values()) {
    const count = Object.values(c.w).reduce((a, x) => a + x, 0);
    if (count <= 0) continue;
    const main = Object.entries(c.w).sort((a, b) => b[1] - a[1])[0][0] as Origin;
    out.push({ lat: c.cj / 10, lon: c.ci / 10, count, frp_sum: c.f, main_origin: main });
  }
  const st = new Map(status.map((s) => [s.d, s.h]));
  const missing = [...eachDay(q.start, q.end)].filter((d) => st.get(d) !== 'full');
  return envelope(q.bbox, q.types, {
    res: 0.1, total: out.reduce((a, c) => a + c.count, 0), max_count: Math.max(0, ...out.map((c) => c.count)), cells: out,
  }, { start: q.start, end: q.end, missing, warnings: warnings(status, new Set(rows.map((r) => r.o)), q.bbox, q.start) });
}

interface MonthSum { y: number; m: number; o: string; c: number }
interface MonthStatus { y: number; m: number; obs: number; allfull: boolean | null }

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

const monthSql = (boxWhere: string, extra = 'true') => `
  SELECT year::INT AS y, month::INT AS m, origin AS o, SUM(count)::DOUBLE AS c FROM hist_cells_month WHERE ${boxWhere} AND ${extra.replace(/\$Y/g, 'year').replace(/\$M/g, 'month')} GROUP BY ALL
  UNION ALL
  SELECT year(fire_date)::INT, month(fire_date)::INT, origin, SUM(count)::DOUBLE FROM live_cells WHERE ${boxWhere} AND ${extra.replace(/\$Y/g, 'year(fire_date)').replace(/\$M/g, 'month(fire_date)')} GROUP BY ALL`;

export async function calendar(b: BBox, types: number[]): Promise<Envelope<CalendarData>> {
  checkBox(b);
  const m = await dataset();
  const [sums, status, statusAll] = await Promise.all([
    query<MonthSum>(monthSql(boxSql(b, types))),
    query<MonthStatus>(`SELECT year(fire_date)::INT AS y, month(fire_date)::INT AS m,
                          COUNT(*) FILTER (WHERE has_data <> 'none')::INT AS obs,
                          bool_and(has_data = 'full') FILTER (WHERE has_data <> 'none') AS allfull
                        FROM all_status GROUP BY ALL`),
    statusRows('true'),
  ]);
  const t = today();
  const [ty, tm] = [+t.slice(0, 4), +t.slice(5, 7)];
  const [sy, sm] = [+m.record_start.slice(0, 4), +m.record_start.slice(5, 7)];
  const sumsBy = new Map<string, MonthSum[]>();
  for (const s of sums) sumsBy.set(`${s.y}-${s.m}`, [...(sumsBy.get(`${s.y}-${s.m}`) ?? []), s]);
  const stBy = new Map(status.map((s) => [`${s.y}-${s.m}`, s]));
  const cells: CalendarCell[] = [];
  for (let y = sy; y <= ty; y++) {
    for (let mo = y === sy ? sm : 1; mo <= (y === ty ? tm : 12); mo++) {
      const total = daysInMonth(y, mo);
      const mid = ymd(y, mo, 15);
      const s = summary(sumsBy.get(`${y}-${mo}`) ?? [], stBy.get(`${y}-${mo}`), total, (o) => uncertaintyOf(o, mid));
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
  return envelope(b, types, { cells, climatology, scale: { min: 0, max: Math.max(0, ...counts), p95: percentile(counts, 0.95) } }, {
    query: { start_year: sy, end_year: ty },
    warnings: warnings(statusAll.filter((s) => s.fl !== 'provisional'), new Set(sums.map((s) => s.o)), b, m.record_start),
  });
}

function categoryOf(v: number, s: { p10: number; p25: number; p75: number; p90: number }): Category {
  if (v < s.p10) return 'well_below';
  if (v < s.p25) return 'below';
  if (v <= s.p75) return 'normal';
  if (v <= s.p90) return 'above';
  return 'well_above';
}

export async function anomaly(b: BBox, month: string, types: number[], baseline?: [number, number]): Promise<Envelope<AnomalyData>> {
  checkBox(b);
  const m = await dataset();
  const y = +month.slice(0, 4), mo = +month.slice(5, 7);
  const latest = m.latest_fire_date;
  const latestDay = +latest.slice(8, 10);
  const mtd = month === latest.slice(0, 7) && latestDay < daysInMonth(y, mo);
  const toDay = mtd ? latestDay : daysInMonth(y, mo);
  const [first, last] = baseline ?? [2001, y - 1];
  if (baseline && !(first <= last && last <= +latest.slice(0, 4)))
    throw new ApiError('INVALID_DATE_RANGE', 'Choose baseline years inside the record.', 400);
  const years = [...new Set([...Array.from({ length: Math.max(0, last - first + 1) }, (_, i) => first + i), y])]
    .filter((yy) => ymd(yy, mo, daysInMonth(yy, mo)) >= m.record_start && ymd(yy, mo, 1) <= latest)
    .sort((a, c) => a - c);
  const yearList = ints(years.length ? years : [y]);
  const sums = mtd
    ? await query<MonthSum>(`SELECT year(fire_date)::INT AS y, ${mo} AS m, origin AS o, SUM(count)::DOUBLE AS c FROM all_cells
                             WHERE month(fire_date) = ${mo} AND day(fire_date) <= ${toDay} AND year(fire_date) IN ${yearList}
                               AND ${boxSql(b, types)} GROUP BY ALL`)
    : await query<MonthSum>(monthSql(boxSql(b, types), `$M = ${mo} AND $Y IN ${yearList}`));
  const status = await query<MonthStatus>(`SELECT year(fire_date)::INT AS y, ${mo} AS m,
                    COUNT(*) FILTER (WHERE has_data <> 'none')::INT AS obs,
                    bool_and(has_data = 'full') FILTER (WHERE has_data <> 'none') AS allfull
                  FROM all_status WHERE month(fire_date) = ${mo} AND day(fire_date) <= ${toDay} AND year(fire_date) IN ${yearList} GROUP BY ALL`);
  const stBy = new Map(status.map((s) => [s.y, s]));
  const history = years.map((yy) => {
    const expected = Math.min(daysInMonth(yy, mo), toDay);
    const s = summary(sums.filter((x) => x.y === yy), stBy.get(yy), expected, (o) => uncertaintyOf(o, ymd(yy, mo, 15)));
    return { year: yy, value: s.count, has_data: s.has_data, main_origin: s.main_origin };
  });
  const current = history.find((h) => h.year === y) ?? { year: y, value: null, has_data: 'none' as HasData, main_origin: null };
  const inBaseline = first <= y && y <= last;
  const values = history.filter((h) => h.has_data === 'full' && h.year >= first && h.year <= last).map((h) => h.value!);
  const counted = inBaseline && current.has_data === 'full';
  const value = current.value;
  const mean = values.length ? values.reduce((a, x) => a + x, 0) / values.length : 0;
  const std = values.length ? Math.sqrt(values.reduce((a, x) => a + (x - mean) ** 2, 0) / values.length) : 0;
  const stats = { p10: percentile(values, 0.1), p25: percentile(values, 0.25), p75: percentile(values, 0.75), p90: percentile(values, 0.9) };
  const median = percentile(values, 0.5);
  const st = await statusRows(`month(fire_date) = ${mo} AND year(fire_date) = ${y} AND day(fire_date) <= ${toDay}`);
  const w = warnings(st, new Set(sums.filter((x) => x.y === y).map((x) => x.o)), b, ymd(y, mo, 1));
  if (mtd) w.unshift({ code: 'MONTH_IN_PROGRESS', message: `Month to date: days 1–${toDay} compared with the same days in other years.` });
  return envelope(b, types, {
    month, compared_days: { from_day: 1, to_day: toDay, month_to_date: mtd }, value,
    baseline: { years: [first, last], n_years: values.length, median, mean, std, ...stats },
    percent_of_median: value !== null && median ? (100 * value) / median : null,
    z_score: value !== null && std ? (value - mean) / std : null,
    rank: value !== null ? 1 + values.filter((x) => x > value).length : null,
    rank_of: values.length + (value !== null && !counted ? 1 : 0),
    category: value !== null && values.length ? categoryOf(value, stats) : null,
    has_data: current.has_data,
    history,
  }, { query: { month, baseline_start_year: first, baseline_end_year: last }, warnings: w });
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

export async function hotspotsCsv(q: AreaQuery): Promise<Uint8Array> {
  checkBox(q.bbox);
  if (boxAreaDeg2(q.bbox) * daysBetween(q.start, q.end) > LIMITS.csv_max_deg2_days)
    throw new ApiError('QUERY_TOO_LARGE', 'Too large for one CSV. Choose a smaller area or shorter period.', 400);
  const m = await dataset();
  // Detection rows are only needed here, so their files are opened only now, and only the ones the
  // period touches (opening each remote file costs a couple of round trips).
  const months = m.live_months.filter((mo) => mo >= q.start.slice(0, 7) && mo <= q.end.slice(0, 7));
  const files = [
    ...histRowFiles(m, q.start, q.end),
    ...months.map((mo) => fileUrl(`live/rows/${mo}.parquet?v=${encodeURIComponent(monthVersion(m, mo))}`)),
  ];
  if (!files.length) throw new ApiError('NOT_FOUND', 'No detections are stored for this period yet.', 404);
  const cols = CSV_COLUMNS.map((c) => (c === 'acq_date' || c === 'fire_date' ? `strftime(${c}, '%Y-%m-%d') AS ${c}` : `"${c}"`)).join(', ');
  return exportFile(`
    COPY (SELECT ${cols} FROM read_parquet([${files.map(str).join(', ')}], union_by_name = true)
          WHERE fire_date BETWEEN ${day(q.start)} AND ${day(q.end)} AND ${boxSql(q.bbox, q.types, 'latitude', 'longitude')}
          ORDER BY acq_date, acq_time) TO 'hotspots.csv' (HEADER, DELIMITER ',')`, 'hotspots.csv');
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
