import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import type { AreaQuery, BBox } from '../api/types';
import { useSelection } from '../store/useSelection';
import { clipBox, monthRange, ym } from '../lib/dates';

const round = (b: BBox): BBox => ({
  min_lat: +b.min_lat.toFixed(3), max_lat: +b.max_lat.toFixed(3),
  min_lon: +b.min_lon.toFixed(3), max_lon: +b.max_lon.toFixed(3),
});

/** keepPreviousData would keep showing the last area's numbers after the area becomes invalid. */
function gated<R extends { data: unknown }>(r: R, on: boolean): R {
  return on ? r : { ...r, data: undefined };
}

export function useMeta() {
  return useQuery({ queryKey: ['meta'], queryFn: api.meta, staleTime: 30 * 60_000 });
}

/** The box to query: the drawn area clipped to coverage, or the whole coverage box. */
export function useBox(): { box: BBox | null; outside: boolean; clipped: boolean } {
  const { data: meta } = useMeta();
  const aoi = useSelection((s) => s.aoi);
  if (!meta) return { box: null, outside: false, clipped: false };
  const cov = meta.coverage.bbox;
  if (!aoi) return { box: cov, outside: false, clipped: false };
  const c = clipBox(aoi, cov);
  if (!c) return { box: null, outside: true, clipped: false };
  const clipped = c.min_lat !== aoi.min_lat || c.max_lat !== aoi.max_lat || c.min_lon !== aoi.min_lon || c.max_lon !== aoi.max_lon;
  return { box: round(c), outside: false, clipped };
}

/** Fire-day range of the selected month, clipped to the record. */
export function useMonthQuery(): AreaQuery | null {
  const { data: meta } = useMeta();
  const { box } = useBox();
  const { year, month, types } = useSelection();
  if (!meta || !box) return null;
  const r = monthRange(year, month, meta.coverage.record_start, meta.coverage.latest_fire_date);
  return r ? { bbox: box, start: r.start, end: r.end, types } : null;
}

export function useCalendar() {
  const { box } = useBox();
  const types = useSelection((s) => s.types);
  return gated(useQuery({
    queryKey: ['calendar', box, types],
    queryFn: () => api.calendar(box!, types),
    enabled: !!box,
    placeholderData: keepPreviousData,
  }), !!box);
}

export function useAnomaly() {
  const { box } = useBox();
  const { year, month, types } = useSelection();
  return gated(useQuery({
    queryKey: ['anomaly', box, year, month, types],
    queryFn: () => api.anomaly(box!, ym(year, month), types),
    enabled: !!box,
    placeholderData: keepPreviousData,
  }), !!box);
}

export function useDaily() {
  const q = useMonthQuery();
  return gated(useQuery({ queryKey: ['daily', q], queryFn: () => api.daily(q!), enabled: !!q, placeholderData: keepPreviousData }), !!q);
}

export function useGrid() {
  const q = useMonthQuery();
  return gated(useQuery({ queryKey: ['grid', q], queryFn: () => api.grid(q!), enabled: !!q, placeholderData: keepPreviousData }), !!q);
}
