import { useCallback, useEffect } from 'react';
import { useMeta } from './useData';
import { useSelection } from '../store/useSelection';
import { LIVE_ENABLED } from './useLiveSync';

/** Previous / next month within the record, plus ← → keyboard shortcuts. */
export function useMonthNav() {
  const { data: meta } = useMeta();
  const { year, month, setMonth } = useSelection();
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
      if (t.closest('input, textarea, select, [contenteditable="true"], .maplibregl-map') || e.altKey || e.ctrlKey || e.metaKey) return;
      if (e.key === 'ArrowLeft') go(e.shiftKey ? -12 : -1);
      if (e.key === 'ArrowRight') go(e.shiftKey ? 12 : 1);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [go]);

  return { prev: () => go(-1), next: () => go(1), canPrev, canNext };
}
