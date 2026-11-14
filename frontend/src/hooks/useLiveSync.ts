import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { create } from 'zustand';
import { USING_ENGINE } from '../api/client';
import { WORKER, dataset, ensureDays, pendingDays } from '../api/engine';
import { useSelection } from '../store/useSelection';
import { daysInMonth, maxDate, minDate, ymd } from '../lib/dates';

interface LiveState { pending: string[]; error: string | null; set: (s: Partial<Pick<LiveState, 'pending' | 'error'>>) => void }
export const useLive = create<LiveState>((set) => ({ pending: [], error: null, set: (s) => set(s) }));

export const LIVE_ENABLED = USING_ENGINE && !!WORKER;
const POLL_MS = 15_000;
const asked = new Set<string>(); // months already requested this session

/**
 * Days after the processed CSV are added on demand: when the selected month has days that
 * aren't stored yet, ask the Worker to process them (FIRMS VIIRS, or MODIS -> Harmonic when
 * VIIRS is offline), then poll until they're stored and refresh every view.
 */
export function useLiveSync() {
  const { year, month } = useSelection();
  const qc = useQueryClient();
  const set = useLive((s) => s.set);

  useEffect(() => {
    if (!LIVE_ENABLED) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    (async () => {
      const m = await dataset();
      const today = new Date().toISOString().slice(0, 10);
      const afterCsv = new Date(Date.parse(`${m.local_end}T00:00:00Z`) + 86_400_000).toISOString().slice(0, 10);
      const start = maxDate(ymd(year, month, 1), afterCsv);
      const end = minDate(ymd(year, month, daysInMonth(year, month)), today);
      const key = `${year}-${month}`;
      if (start <= end && !asked.has(key)) {
        asked.add(key);
        try {
          const r = await ensureDays(start, end);
          if (r?.error) console.warn('AGNI live processing:', r.error);
          if (r) set({ pending: r.pending, error: r.error ? 'Some recent days can’t be fetched right now. Older months still work.' : null });
        } catch (e) {
          console.warn('AGNI live processing:', e);
          set({ error: 'Some recent days can’t be fetched right now. Older months still work.' });
        }
      }

      // poll while anything is pending; reload the dataset whenever the pipeline publishes
      let seen = m.updated_at;
      const tick = async () => {
        if (stopped) return;
        try {
          const pending = await pendingDays();
          const fresh = await dataset(true);
          if (fresh.updated_at !== seen) {
            seen = fresh.updated_at;
            await qc.invalidateQueries();
          }
          set({ pending });
          if (pending.length) timer = setTimeout(tick, POLL_MS);
        } catch {
          timer = setTimeout(tick, POLL_MS * 2);
        }
      };
      if (useLive.getState().pending.length) timer = setTimeout(tick, POLL_MS);
    })();

    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }, [year, month, qc, set]);
}
