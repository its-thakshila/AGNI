// Client for the AGNI API (docs/API_CONTRACT.md v1.2).
// Default: answered in the browser (plain JS over hyparquet) from the dataset files
// (VITE_DATA_URL, or the bundled mock dataset). With VITE_API_BASE set, calls go to the
// FastAPI backend instead (useful for backend development).

import type {
  AnomalyData, AreaQuery, BBox, CalendarData, DailyData, Envelope, GridData, Meta, ApiErrorBody,
} from './types';
import * as engine from './engine';
import { ApiError } from './errors';

export { ApiError };

const BASE = ((import.meta.env.VITE_API_BASE as string | undefined) ?? '').replace(/\/$/, '');
export const USING_ENGINE = !BASE;
const TIMEOUT_MS = 60_000;

/** Plain-language messages for the error codes in contract §11. */
export function friendlyError(e: unknown): string {
  if (!(e instanceof ApiError)) return 'Something went wrong. Please try again.';
  switch (e.code) {
    case 'BOX_OUTSIDE_COVERAGE': return 'That area is outside the region we have data for. Draw an area inside Australia.';
    case 'EMPTY_BOX': return 'The area is too small. Draw a bigger box.';
    case 'QUERY_TOO_LARGE': return 'That is too much data at once. Choose a smaller area or a shorter period.';
    case 'INVALID_DATE_RANGE': return 'Those dates are outside the record.';
    case 'UPSTREAM_ERROR': return 'Recent satellite data is temporarily unavailable. Older years still work.';
    case 'TIMEOUT': return 'The server took too long to answer. Please try again.';
    case 'NETWORK': return 'Can’t reach the AGNI data. Check your connection.';
    default: return e.message || 'Something went wrong. Please try again.';
  }
}

const boxParams = (b: BBox) => ({ lat1: b.min_lat, lon1: b.min_lon, lat2: b.max_lat, lon2: b.max_lon });

function url(path: string, params: Record<string, string | number | number[] | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined) q.set(k, Array.isArray(v) ? v.join(',') : String(v));
  return `${BASE}${path}?${q}`;
}

async function get<T>(path: string, params: Record<string, string | number | number[] | undefined> = {}): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  let res: Response;
  try {
    res = await fetch(url(path, params), { signal: ctrl.signal });
  } catch (e) {
    throw new ApiError(ctrl.signal.aborted ? 'TIMEOUT' : 'NETWORK', String(e));
  } finally {
    clearTimeout(timer);
  }
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as ApiErrorBody | null;
    const err = body?.error;
    throw new ApiError(err?.code ?? 'INTERNAL_ERROR', err?.message ?? `HTTP ${res.status}`, res.status, err?.field ?? null, err?.hint ?? null);
  }
  return res.json() as Promise<T>;
}

/** Engine errors that aren't ApiErrors (e.g. a failed file read) become a generic API error. */
async function local<T>(work: Promise<T>): Promise<T> {
  try {
    return await work;
  } catch (e) {
    if (e instanceof ApiError) throw e;
    console.error(e);
    throw new ApiError('INTERNAL_ERROR', 'Could not read the fire data. Please try again.');
  }
}

const areaParams = (q: AreaQuery) => ({ ...boxParams(q.bbox), start: q.start, end: q.end, types: q.types });

export const api = {
  meta: (): Promise<Meta> => (USING_ENGINE ? local(engine.meta()) : get('/api/meta')),

  calendar: (bbox: BBox, types: number[]): Promise<Envelope<CalendarData>> =>
    USING_ENGINE ? local(engine.calendar(bbox, types)) : get('/api/calendar', { ...boxParams(bbox), types }),

  /** month = "YYYY-MM"; the month in progress is compared month-to-date.
   *  baseline = [first, last] year; omit for the default (2001 to the year before the month). */
  anomaly: (bbox: BBox, month: string, types: number[], baseline?: [number, number]): Promise<Envelope<AnomalyData>> =>
    USING_ENGINE
      ? local(engine.anomaly(bbox, month, types, baseline))
      : get('/api/anomaly', { ...boxParams(bbox), month, types, baseline_start_year: baseline?.[0], baseline_end_year: baseline?.[1] }),

  /** one day (YYYY-MM-DD) compared with the same date in other years; browser engine only */
  dayAnomaly: (bbox: BBox, date: string, types: number[]): Promise<Envelope<AnomalyData>> =>
    USING_ENGINE
      ? local(engine.dayAnomaly(bbox, date, types))
      : Promise.reject(new ApiError('NOT_AVAILABLE', 'Day comparison needs the in-browser data engine.')),

  daily: (q: AreaQuery): Promise<Envelope<DailyData>> => (USING_ENGINE ? local(engine.daily(q)) : get('/api/daily', areaParams(q))),

  grid: (q: AreaQuery): Promise<Envelope<GridData>> => (USING_ENGINE ? local(engine.grid(q)) : get('/api/grid', areaParams(q))),
};

/** Download the hotspots CSV for a query (contract §9). */
export async function downloadCsv(q: AreaQuery) {
  const b = q.bbox;
  const name = `hotspots_${b.min_lat}_${b.min_lon}_${b.max_lat}_${b.max_lon}_${q.start}_${q.end}.csv`;
  if (USING_ENGINE) {
    const csv = await local(engine.hotspotsCsv(q));
    const href = URL.createObjectURL(new Blob([csv as BlobPart], { type: 'text/csv' }));
    save(href, name);
    setTimeout(() => URL.revokeObjectURL(href), 1000);
    return;
  }
  // The server streams it with Content-Disposition: attachment, so a plain navigation saves the file.
  save(url('/api/hotspots.csv', areaParams(q)));
}

function save(href: string, filename?: string) {
  const a = Object.assign(document.createElement('a'), { href, rel: 'noopener' });
  if (filename) a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
}
