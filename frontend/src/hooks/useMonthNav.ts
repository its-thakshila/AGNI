import { useCallback, useEffect } from 'react';
import { useMeta, useMonthQuery } from './useData';
import { monthRange } from '../lib/dates';
import { useSelection } from '../store/useSelection';
import { LIVE_ENABLED } from './useLiveSync';

/** Previous / next month within the record, plus ← → keyboard shortcuts. */
export function useMonthNav() {
  const { data: meta } = useMeta();
  const { year, month, day, setMonth } = useSelection();
  const dayStep = useDayStep();
  const idx = year * 12 + (month - 1);
  const min = meta ? +meta.coverage.record_start.slice(0, 4) * 12 + (+meta.coverage.record_start.slice(5, 7) - 1) : -Infinity;
  // with live processing, months up to today can be opened (they're fetched on demand)
  const now = new Date();
  const latestIdx = meta ? +meta.coverage.latest_fire_date.slice(0, 4) * 12 + (+meta.coverage.latest_fire_date.slice(5, 7) - 1) : Infinity;
  const max = LIVE_ENABLED ? Math.max(latestIdx, now.getUTCFullYear() * 12 + now.getUTCMonth()) : latestIdx;
  const canPrev = idx > min;
  const canNext = idx < max;

  const go = useCallback(
    (delta: number) => {
      const n = Math.min(max, Math.max(min, idx + delta));
      if (n !== idx) setMonth(Math.floor(n / 12), (n % 12) + 1);
    },
    [idx, min, max, setMonth],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t?.closest?.('input, textarea, select, [contenteditable="true"], .maplibregl-map') || e.altKey || e.ctrlKey || e.metaKey) return;
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      const dir = e.key === 'ArrowLeft' ? -1 : 1;
      // while the map shows one day, the arrows step through days (on into the next month)
      if (day !== null && !e.shiftKey) {
        dayStep.go(dir);
        return;
      }
      go(dir * (e.shiftKey ? 12 : 1));
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [go, day, dayStep]);

  return { prev: () => go(-1), next: () => go(1), canPrev, canNext };
}

/**
 * Step the day shown on the map, across month boundaries: before the 1st comes the previous
 * month's last day, after the last day the next month's first, so days of two months can be
 * compared one after another. Days outside the record (or not in yet) are skipped.
 */
export function useDayStep() {
  const { data: meta } = useMeta();
  const q = useMonthQuery();
  const { year, month, day, setDay, setMonthDay } = useSelection();

  const target = (dir: -1 | 1): [number, number, number] | null => {
    if (!meta || !q || day === null) return null;
    const n = day + dir;
    if (n >= +q.start.slice(8, 10) && n <= +q.end.slice(8, 10)) return [year, month, n];
    const idx = year * 12 + (month - 1) + dir;
    const [y, m] = [Math.floor(idx / 12), (idx % 12) + 1];
    const r = monthRange(y, m, meta.coverage.record_start, meta.coverage.latest_fire_date);
    if (!r) return null;
    return [y, m, +(dir < 0 ? r.end : r.start).slice(8, 10)];
  };

  const go = (dir: -1 | 1) => {
    const t = target(dir);
    if (!t) return;
    if (t[0] === year && t[1] === month) setDay(t[2]);
    else setMonthDay(...t);
  };

  return { go, canPrev: target(-1) !== null, canNext: target(1) !== null };
}
