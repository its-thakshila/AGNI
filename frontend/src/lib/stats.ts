export const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
export const MONTHS_LONG = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];

/** Counts are weighted floats (contract §1.2): always round for display. */
export const fmt = (n: number) => Math.round(n).toLocaleString('en-US');

export function compact(n: number): string {
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e4) return `${Math.round(n / 1e3)}k`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}k`;
  if (n < 10 && n % 1) return n.toFixed(1); // small boxes: axis ticks like 0.5 must not round to "1"
  return String(Math.round(n));
}

/** "2.6× usual", "28% above usual", "about usual", "40% below usual" from percent_of_median. */
export function percentText(pct: number): string {
  if (pct >= 95 && pct <= 105) return 'About usual';
  if (pct >= 200) return `${(pct / 100).toFixed(pct >= 1000 ? 0 : 1)}× usual`;
  if (pct > 100) return `${Math.round(pct - 100)}% above usual`;
  return `${Math.round(100 - pct)}% below usual`;
}

export function ordinal(n: number): string {
  const s = ['th', 'st', 'nd', 'rd'];
  const v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

export function median(xs: number[]): number {
  if (!xs.length) return 0;
  const s = [...xs].sort((a, b) => a - b);
  const mid = s.length >> 1;
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}
