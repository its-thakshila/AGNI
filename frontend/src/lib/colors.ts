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
export const CATEGORY_LABEL: Record<Category, string> = {
  well_below: 'Much lower than usual',
  below: 'Lower than usual',
  normal: 'Usual',
  above: 'Higher than usual',
  well_above: 'Much higher than usual',
};
export const CATEGORIES: Category[] = ['well_below', 'below', 'normal', 'above', 'well_above'];

export const NO_DATA = '#1f1f23';
export const INK = { primary: '#f0efec', secondary: '#b9b8b2', muted: '#84838c', line: '#26262c', page: '#0b0b0d', raised: '#1a1a1e' };

export function pick(ramp: string[], t: number): string {
  const x = Math.max(0, Math.min(1, t));
  return ramp[Math.min(ramp.length - 1, Math.floor(x * ramp.length))];
}

export const ORIGIN_LABEL: Record<Origin, string> = {
  real: 'Measured by VIIRS',
  ml: 'Rebuilt from MODIS (model)',
  gapfill: 'Filled by the model (satellite outage)',
  firms_nrt: 'Measured by VIIRS (near real time)',
  firms_sp: 'Measured by VIIRS (final archive)',
};
