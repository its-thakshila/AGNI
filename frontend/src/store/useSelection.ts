import { create } from 'zustand';
import type { BBox } from '../api/types';

export type CalendarMode = 'count' | 'anomaly';
export type Tab = 'calendar' | 'month' | 'trends';
export type CalView = 'months' | 'days';

interface SelectionState {
  /** area the user drew or typed (not yet clipped to coverage); null = whole region */
  aoi: BBox | null;
  year: number;
  month: number;
  /** a single day of the selected month shown on the map; null = the whole month */
  day: number | null;
  mode: CalendarMode;
  /** the Calendar tab shows every month, or the selected month's days */
  calView: CalView;
  tab: Tab;
  types: number[];
  drawing: boolean;
  setAoi: (b: BBox | null) => void;
  setMonth: (year: number, month: number) => void;
  setDay: (day: number | null) => void;
  /** jump to a day of another month (stepping days across a month boundary) */
  setMonthDay: (year: number, month: number, day: number) => void;
  setMode: (m: CalendarMode) => void;
  setCalView: (v: CalView) => void;
  setTab: (t: Tab) => void;
  setTypes: (t: number[]) => void;
  setDrawing: (d: boolean) => void;
}

// Start on the month of the latest fire-day (yesterday): "how is this month going?"
const latest = new Date(Date.now() - 86_400_000);

export const useSelection = create<SelectionState>((set) => ({
  aoi: null,
  year: latest.getUTCFullYear(),
  month: latest.getUTCMonth() + 1,
  day: null,
  mode: 'count',
  calView: 'months',
  tab: 'calendar',
  types: [0],
  drawing: false,
  setAoi: (aoi) => set({ aoi, drawing: false }),
  setMonth: (year, month) => set({ year, month, day: null }),
  setDay: (day) => set({ day }),
  setMonthDay: (year, month, day) => set({ year, month, day }),
  setMode: (mode) => set({ mode }),
  setCalView: (calView) => set({ calView }),
  setTab: (tab) => set({ tab }),
  setTypes: (types) => set({ types }),
  setDrawing: (drawing) => set({ drawing }),
}));
