import type { Category, Origin } from '../api/types';

// Sequential "ember" ramp: one hue (orange), dark -> light, so near-zero
// recedes into the dark surface and intense months glow.
export const EMBER = ['#2a1b14', '#47231a', '#6b2d1c', '#93391d', '#bb4a1f', '#de6026', '#f2803d', '#f8a466', '#fcc896'];

// Diverging, keyed on the contract's five categories: blue (less) <-> grey <-> red (more).
export const CATEGORY_COLOR: Record<Category, string> = {
  well_below: '#2a78d6',
  below: '#3b5f8f',
  normal: '#383835',
  above: '#a84646',
  well_above: '#e66767',
};
// The category is about rank among the years (percentiles), not size: the % beside it gives the
// size. Worded that way so "6% below usual · Among the lowest" or "77% above usual · Higher than
// most years" never read as a contradiction.
export const CATEGORY_LABEL: Record<Category, string> = {
  well_below: 'Among the lowest years',   // bottom 10%
  below: 'Lower than most years',         // 10-25%
  normal: 'Within the usual range',       // middle half
  above: 'Higher than most years',        // 75-90%
  well_above: 'Among the highest years',  // top 10%
};
export const CATEGORIES: Category[] = ['well_below', 'below', 'normal', 'above', 'well_above'];

export const NO_DATA = '#1f1f23';
export const INK = { primary: '#f0efec', secondary: '#b9b8b2', muted: '#84838c', line: '#26262c', page: '#0b0b0d', raised: '#1a1a1e' };

export function pick(ramp: string[], t: number): string {
  const x = Math.max(0, Math.min(1, t));
  return ramp[Math.min(ramp.length - 1, Math.floor(x * ramp.length))];
}

/** Where a number comes from, in the same words everywhere in the app. */
export const ORIGIN_LABEL: Record<Origin, string> = {
  real: 'Observed by VIIRS',
  ml: 'Harmonized from MODIS by Harmonic',
  gapfill: 'Gap-filled by Harmonic',
  firms_nrt: 'Observed by VIIRS · near real time',
  firms_sp: 'Observed by VIIRS · final archive',
};

/** One-paragraph explanation behind each label (shown on demand). */
export const ORIGIN_DETAIL: Record<Origin, string> = {
  real: 'Counted directly from VIIRS satellite passes (Suomi-NPP, 375 m pixels).',
  ml: 'VIIRS was not flying yet. Harmonic, AGNI’s harmonization model, converted MODIS observations into VIIRS-equivalent detections, so this year compares fairly with recent ones. Reliable as monthly totals (about ±3–5%), not as exact fire locations.',
  gapfill: 'VIIRS had an outage on some of these days. Harmonic, AGNI’s harmonization model, filled them from MODIS observations, as VIIRS-equivalent detections.',
  firms_nrt: 'Recent VIIRS data from NASA FIRMS. Near-real-time values can still change for about 3 days.',
  firms_sp: 'Final, quality-checked VIIRS archive from NASA FIRMS.',
};
