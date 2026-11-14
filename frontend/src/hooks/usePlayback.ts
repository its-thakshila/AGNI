import { useEffect, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import { useMonthQuery } from './useData';
import { useSelection } from '../store/useSelection';
import { ymd } from '../lib/dates';
import { shapesFor } from '../lib/shapeCache';
import type { Envelope, GridData } from '../api/types';

/** Time each day stays on the map while playing at 1× (the map crossfades between days). */
export const FRAME_MS = 650;

/**
 * Plays the map through the selected month, one day per frame. Each day's map is fetched ahead
 * (the rest of the month in the background); a frame only advances once the next day's map is in,
 * so playback never shows a stale day. Stops at the month's last day.
 */
export function usePlayback() {
  const qc = useQueryClient();
  const q = useMonthQuery();
  const { year, month, day, playing, speed, setDay, setPlaying } = useSelection();
  const frame = FRAME_MS / speed;

  // As soon as playback starts: fetch every day of the month, and draw the days ahead of the one
  // shown, one per task (so the page stays responsive), so high speeds never wait on drawing.
  const dayRef = useRef(day);
  dayRef.current = day;
  useEffect(() => {
    if (!playing || !q) return;
    let cancelled = false;
    const first = +q.start.slice(8, 10), last = +q.end.slice(8, 10);
    const loads = new Map<number, Promise<Envelope<GridData>>>();
    for (let d = first; d <= last; d++) {
      const dq = { ...q, start: ymd(year, month, d), end: ymd(year, month, d) };
      loads.set(d, qc.fetchQuery({ queryKey: ['grid', dq], queryFn: () => api.grid(dq), staleTime: 10 * 60_000 }));
    }
    (async () => {
      for (let d = Math.max(first, (dayRef.current ?? first) + 1); d <= last && !cancelled; d++) {
        try { shapesFor((await loads.get(d)!).data); } catch { /* the map shows the error when it gets there */ }
        await new Promise((r) => setTimeout(r, 0));
      }
    })();
    return () => { cancelled = true; };
    // q is rebuilt each render; the month/area/types identify it
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, q?.start, q?.end, q?.bbox, q?.types, year, month, qc]);

  // pressing play on the month's last day (e.g. after a full run) starts again from the first
  const wasPlaying = useRef(false);
  useEffect(() => {
    const started = playing && !wasPlaying.current;
    wasPlaying.current = playing;
    if (started && q && day !== null && day >= +q.end.slice(8, 10)) setDay(+q.start.slice(8, 10));
  }, [playing, day, q, setDay]);

  useEffect(() => {
    if (!playing || !q) return;
    const first = +q.start.slice(8, 10), last = +q.end.slice(8, 10);
    if (day === null || day < first || day > last) { setDay(first); return; }
    if (day >= last) {
      const t = setTimeout(() => setPlaying(false), frame);
      return () => clearTimeout(t);
    }
    const next = { ...q, start: ymd(year, month, day + 1), end: ymd(year, month, day + 1) };
    let timer: ReturnType<typeof setTimeout>;
    const started = Date.now();
    let ready = false;
    const tick = () => {
      // wait for the next day's map rather than skipping ahead of the data; once it is in, draw its
      // shapes now (while this day is still showing), so the switch itself is instant
      const g = qc.getQueryData<Envelope<GridData>>(['grid', next]);
      if (g && !ready) { shapesFor(g.data); ready = true; }
      if (ready && Date.now() - started >= frame) setDay(day + 1);
      else timer = setTimeout(tick, Math.max(10, Math.min(60, frame - (Date.now() - started))));
    };
    timer = setTimeout(tick, 0);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, day, year, month, frame, q?.start, q?.end, setDay, setPlaying, qc]);
}
