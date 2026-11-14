/** Overlay ink (text, hatching) that stays visible on both dark and light calendar cells. */
export function overlayInk(hex: string, alpha: number): string {
  const n = parseInt(hex.slice(1), 16);
  const lum = (0.299 * (n >> 16) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255)) / 255;
  return lum > 0.45 ? `rgba(11,11,13,${alpha + 0.2})` : `rgba(240,239,236,${alpha})`;
}
