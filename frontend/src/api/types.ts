// Types from docs/API_CONTRACT.md (contract v1.1, §13). Keep in sync with the backend.

export type HasData = 'full' | 'partial' | 'none';
export type Origin = 'real' | 'ml' | 'gapfill' | 'firms_nrt' | 'firms_sp';
export type Category = 'well_below' | 'below' | 'normal' | 'above' | 'well_above';

export interface BBox { min_lat: number; max_lat: number; min_lon: number; max_lon: number }
export interface Warning { code: string; message: string }

export interface Envelope<T> {
  query: { bbox: BBox; start?: string; end?: string; types: number[]; [k: string]: unknown };
  partial: boolean;
  missing_dates: string[];
  warnings: Warning[];
  generated_at: string;
  data: T;
}

export interface DailyValue {
  fire_date: string;
  count: number | null;
  count_day: number | null;
  count_night: number | null;
  frp_sum: number | null;
  n_rows: number;
  origin_mix: Partial<Record<Origin, number>>;
  has_data: HasData;
  uncertainty: number | null;
  daynight_reliable: boolean;
}
export interface DailyData {
  days: DailyValue[];
  totals: { count: number; frp_sum: number; days_with_data: number; days_total: number };
}

export interface CalendarCell {
  year: number; month: number;
  count: number | null; has_data: HasData;
  days_with_data: number; days_total: number; complete: boolean;
  main_origin: Origin | null; uncertainty: number | null;
}
export interface Climatology {
  month: number; median: number; p10: number; p25: number; p75: number; p90: number;
  min: number; max: number; years_used: number;
}
export interface CalendarData {
  cells: CalendarCell[];
  climatology: Climatology[];
  scale: { min: number; max: number; p95: number };
}

export interface AnomalyData {
  month: string;
  compared_days: { from_day: number; to_day: number; month_to_date: boolean };
  value: number | null;
  baseline: {
    years: [number, number]; n_years: number;
    median: number; mean: number; std: number;
    p10: number; p25: number; p75: number; p90: number;
  };
  percent_of_median: number | null;
  z_score: number | null;
  rank: number | null;
  rank_of: number;
  category: Category | null;
  has_data: HasData;
  history: { year: number; value: number | null; has_data: HasData; main_origin: Origin | null }[];
}

export interface GridCell { lat: number; lon: number; count: number; frp_sum: number; main_origin: Origin }
export interface GridData { res: number; total: number; max_count: number; cells: GridCell[] }

export interface Hotspot {
  latitude: number; longitude: number; bright_ti4: number; scan: number; track: number;
  acq_date: string; acq_time: string; satellite: string; instrument: string;
  confidence: 'l' | 'n' | 'h'; version: string; bright_ti5: number; frp: number;
  daynight: 'D' | 'N'; type: number;
  fire_date: string; pass: 'D' | 'N'; origin: Origin; weight: number;
}

export interface Meta {
  coverage: { record_start: string; local_end: string; latest_fire_date: string; bbox: BBox };
  periods: { from: string; to: string | null; origin: Origin; label: string; uncertainty: number }[];
  known_gaps: { from: string; to: string; reason: string }[];
  types: { value: number; label: string }[];
  limits: {
    hotspots_max_limit: number; small_box_deg: number;
    hotspots_max_deg2_days: number; csv_max_deg2_days: number; grid_max_days: number;
  };
  firms: { status: string; last_fetch: string | null };
  contract_version: string;
}

export interface ApiErrorBody {
  error: { code: string; message: string; field: string | null; hint: string | null };
}

/** Common query: box + fire-day range + hotspot types. */
export interface AreaQuery { bbox: BBox; start: string; end: string; types: number[] }

export const isModelled = (o: Origin | null | undefined) => o === 'ml' || o === 'gapfill';
