import { create } from 'zustand';
import type { BBox } from '../api/types';

export type CalendarMode = 'count' | 'anomaly';
export type Tab = 'calendar' | 'month' | 'trends';

interface SelectionState {
  /** area the user drew or typed (not yet clipped to coverage); null = whole region */
  aoi: BBox | null;
  year: number;
  month: number;
  mode: CalendarMode;
  tab: Tab;
  types: number[];
  drawing: boolean;
  setAoi: (b: BBox | null) => void;
  setMonth: (year: number, month: number) => void;
  setMode: (m: CalendarMode) => void;
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
  mode: 'count',
  tab: 'calendar',
  types: [0],
  drawing: false,
  setAoi: (aoi) => set({ aoi, drawing: false }),
  setMonth: (year, month) => set({ year, month }),
  setMode: (mode) => set({ mode }),
  setTab: (tab) => set({ tab }),
  setTypes: (types) => set({ types }),
  setDrawing: (drawing) => set({ drawing }),
}));
