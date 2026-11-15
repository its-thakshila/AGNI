import { create } from 'zustand';
import type { BBox, Lead } from '../api/types';
import type { Basemap } from '../lib/basemaps';

export type CalendarMode = 'count' | 'anomaly';
export type Tab = 'calendar' | 'month' | 'trends' | 'forecast';
export type CalView = 'months' | 'days';
export const PLAY_SPEEDS = [0.5, 1, 2, 4, 8] as const;
export type PlaySpeed = (typeof PLAY_SPEEDS)[number];

interface SelectionState {
  /** area the user drew or typed (not yet clipped to coverage); null = whole region */
  aoi: BBox | null;
  year: number;
  month: number;
  /** a single day of the selected month shown on the map; null = the whole month */
  day: number | null;
  /** the map is playing through the month's days */
  playing: boolean;
  /** playback speed: days per frame-time multiplier (1 = about 1.5 days a second) */
  speed: PlaySpeed;
  /** map background (remembered in this browser) */
  basemap: Basemap;
  mode: CalendarMode;
  /** the Calendar tab shows every month, or the selected month's days */
  calView: CalView;
  tab: Tab;
  /** Forecast tab: days ahead shown on the map */
  lead: Lead;
  types: number[];
  drawing: boolean;
  setAoi: (b: BBox | null) => void;
  setMonth: (year: number, month: number) => void;
  setDay: (day: number | null) => void;
  setPlaying: (playing: boolean) => void;
  setSpeed: (speed: PlaySpeed) => void;
  setBasemap: (b: Basemap) => void;
  /** jump to a day of another month (stepping days across a month boundary) */
  setMonthDay: (year: number, month: number, day: number) => void;
  setMode: (m: CalendarMode) => void;
  setCalView: (v: CalView) => void;
  setTab: (t: Tab) => void;
  setLead: (l: Lead) => void;
  setTypes: (t: number[]) => void;
  setDrawing: (d: boolean) => void;
}

function savedBasemap(): Basemap {
  try {
    const v = localStorage.getItem('agni.basemap');
    if (v === 'dark' || v === 'satellite' || v === 'daily' || v === 'terrain') return v;
  } catch { /* storage unavailable */ }
  return 'dark';
}

// Start on the month of the latest fire-day (yesterday): "how is this month going?"
const latest = new Date(Date.now() - 86_400_000);

export const useSelection = create<SelectionState>((set) => ({
  aoi: null,
  year: latest.getUTCFullYear(),
  month: latest.getUTCMonth() + 1,
  day: null,
  playing: false,
  speed: 1,
  basemap: savedBasemap(),
  mode: 'count',
  calView: 'months',
  tab: 'calendar',
  lead: 1,
  types: [0],
  drawing: false,
  setAoi: (aoi) => set({ aoi, drawing: false, playing: false }),
  setMonth: (year, month) => set({ year, month, day: null, playing: false }),
  setDay: (day) => set(day === null ? { day, playing: false } : { day }),
  setPlaying: (playing) => set({ playing }),
  setSpeed: (speed) => set({ speed }),
  setBasemap: (basemap) => {
    try { localStorage.setItem('agni.basemap', basemap); } catch { /* private mode etc. */ }
    set({ basemap });
  },
  setMonthDay: (year, month, day) => set({ year, month, day }),
  setMode: (mode) => set({ mode }),
  setCalView: (calView) => set({ calView }),
  // the forecast shows the days ahead: stop playback and a selected day of the past
  setTab: (tab) => set(tab === 'forecast' ? { tab, playing: false, day: null } : { tab }),
  setLead: (lead) => set({ lead }),
  setTypes: (types) => set({ types }),
  setDrawing: (drawing) => set({ drawing }),
}));
