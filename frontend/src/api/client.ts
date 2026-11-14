// Thin client for the AGNI API (docs/API_CONTRACT.md v1.1).
// With no VITE_API_BASE set, every call is answered by the in-browser mock.

import type {
  AnomalyData, AreaQuery, BBox, CalendarData, DailyData, Envelope, GridData, Meta, ApiErrorBody,
} from './types';
import * as mock from './mock';
import { ApiError } from './errors';

export { ApiError };

const BASE = ((import.meta.env.VITE_API_BASE as string | undefined) ?? '').replace(/\/$/, '');
export const USING_MOCK = !BASE;
const TIMEOUT_MS = 60_000; // 2026 days not yet cached can take 5-20 s (contract §14.1)


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
    case 'NETWORK': return 'Can’t reach the AGNI server. Check your connection.';
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

const areaParams = (q: AreaQuery) => ({ ...boxParams(q.bbox), start: q.start, end: q.end, types: q.types });

export const api = {
  meta: (): Promise<Meta> => (USING_MOCK ? mock.meta() : get('/api/meta')),

  calendar: (bbox: BBox, types: number[]): Promise<Envelope<CalendarData>> =>
    USING_MOCK ? mock.calendar(bbox, types) : get('/api/calendar', { ...boxParams(bbox), types }),

  /** month = "YYYY-MM"; the backend compares month-to-date for the current month. */
  anomaly: (bbox: BBox, month: string, types: number[]): Promise<Envelope<AnomalyData>> =>
    USING_MOCK ? mock.anomaly(bbox, month, types) : get('/api/anomaly', { ...boxParams(bbox), month, types }),

  daily: (q: AreaQuery): Promise<Envelope<DailyData>> => (USING_MOCK ? mock.daily(q) : get('/api/daily', areaParams(q))),

  grid: (q: AreaQuery): Promise<Envelope<GridData>> => (USING_MOCK ? mock.grid(q) : get('/api/grid', areaParams(q))),
};

/** Download the hotspots CSV for a query (contract §9). */
export async function downloadCsv(q: AreaQuery) {
  if (USING_MOCK) {
    const csv = await mock.hotspotsCsv(q);
    const href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
    const b = q.bbox;
    save(href, `hotspots_${b.min_lat}_${b.min_lon}_${b.max_lat}_${b.max_lon}_${q.start}_${q.end}.csv`);
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
