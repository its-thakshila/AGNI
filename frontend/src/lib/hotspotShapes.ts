// Turns 0.1° grid cells into smooth, crisp-edged "fluid" shapes for the map.
//
// 1. Each cell drops a weight (0.3 for the quietest cell, 1 for the busiest) on a raster
//    twice as fine as the grid.
// 2. A small Gaussian spreads each weight so touching cells overlap and merge, while
//    separate fires stay separate. The kernel peaks at 1, so a lone cell keeps its weight.
// 3. Marching-squares contours are traced at a few levels (like height lines on a
//    topographic map) and their corners rounded with Chaikin smoothing.
// The result is plain polygons in lon/lat, so they scale correctly at every zoom.

import { contours } from 'd3-contour';
import type { ForecastCell, GridCell } from '../api/types';

export const LEVELS = [0.22, 0.55, 1.0, 1.7];
const UP = 2; // raster cells per grid cell, per axis
const SIGMA = 0.55 * UP; // in raster cells
const PAD = 3; // grid cells of empty margin around the data

function blur1d(src: Float32Array, dst: Float32Array, w: number, h: number, horizontal: boolean, kernel: number[]) {
  const r = kernel.length - 1;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      let s = src[y * w + x] * kernel[0];
      for (let k = 1; k <= r; k++) {
        if (horizontal) {
          if (x - k >= 0) s += src[y * w + x - k] * kernel[k];
          if (x + k < w) s += src[y * w + x + k] * kernel[k];
        } else {
          if (y - k >= 0) s += src[(y - k) * w + x] * kernel[k];
          if (y + k < h) s += src[(y + k) * w + x] * kernel[k];
        }
      }
      dst[y * w + x] = s;
    }
  }
}

/** Chaikin corner cutting on a closed ring; keeps it closed. */
function smooth(ring: number[][], iterations = 2): number[][] {
  let pts = ring.slice(0, -1);
  for (let it = 0; it < iterations && pts.length > 2; it++) {
    const next: number[][] = [];
    for (let i = 0; i < pts.length; i++) {
      const [x0, y0] = pts[i];
      const [x1, y1] = pts[(i + 1) % pts.length];
      next.push([0.75 * x0 + 0.25 * x1, 0.75 * y0 + 0.25 * y1], [0.25 * x0 + 0.75 * x1, 0.25 * y0 + 0.75 * y1]);
    }
    pts = next;
  }
  return [...pts, pts[0]];
}

export function hotspotShapes(cells: GridCell[], maxCount: number, res: number): GeoJSON.FeatureCollection {
  if (!cells.length) return { type: 'FeatureCollection', features: [] };
  const step = res / UP;
  let minLon = Infinity, minLat = Infinity, maxLon = -Infinity, maxLat = -Infinity;
  for (const c of cells) {
    minLon = Math.min(minLon, c.lon);
    minLat = Math.min(minLat, c.lat);
    maxLon = Math.max(maxLon, c.lon + res);
    maxLat = Math.max(maxLat, c.lat + res);
  }
  minLon -= PAD * res; minLat -= PAD * res; maxLon += PAD * res; maxLat += PAD * res;
  const w = Math.ceil((maxLon - minLon) / step);
  const h = Math.ceil((maxLat - minLat) / step);

  // one weight per cell, placed at the raster pixel holding the cell centre (row 0 = north).
  // The cell index is rounded, never floored: cell corners are multiples of 0.1°, and flooring a
  // float like 12.9999999 put some cells one pixel off, so neighbours piled up or left gaps and
  // dense areas showed a stripe/grid pattern.
  const lm = Math.log10(Math.max(1, maxCount) + 1);
  const a = new Float32Array(w * h);
  const half = Math.floor(UP / 2);
  for (const c of cells) {
    const x = Math.min(w - 1, Math.max(0, Math.round((c.lon - minLon) / res) * UP + half));
    const y = Math.min(h - 1, Math.max(0, Math.round((maxLat - c.lat) / res) * UP - UP + half));
    a[y * w + x] += 0.3 + 0.7 * (Math.log10(c.count + 1) / lm);
  }

  const radius = Math.ceil(3 * SIGMA);
  const kernel = Array.from({ length: radius + 1 }, (_, i) => Math.exp(-(i * i) / (2 * SIGMA * SIGMA)));
  const b = new Float32Array(w * h);
  blur1d(a, b, w, h, true, kernel);
  blur1d(b, a, w, h, false, kernel);

  // d3-contour puts pixel (i, j)'s value at (i + 0.5, j + 0.5); a cell's weight sits at pixel
  // (k·UP + half), so shift back by half a pixel to land shapes exactly on the cell centres
  const shift = half + 0.5 - UP / 2;
  const toLonLat = ([x, y]: number[]) => [+(minLon + (x - shift) * step).toFixed(5), +(maxLat - (y - shift) * step).toFixed(5)];
  const bands = contours().size([w, h]).thresholds(LEVELS)(Array.from(a));
  return {
    type: 'FeatureCollection',
    features: bands
      .filter((m) => m.coordinates.length)
      .map((m) => ({
        type: 'Feature',
        properties: { band: LEVELS.indexOf(m.value) },
        geometry: {
          type: 'MultiPolygon',
          coordinates: m.coordinates.map((poly) => poly.map((ring) => smooth(ring).map(toLonLat))),
        },
      })),
  };
}

/** Chance-of-fire bands for the forecast map (probability of at least one detection in a cell). */
export const FORECAST_LEVELS = [0.05, 0.15, 0.3, 0.5];

/**
 * The forecast as the same fluid shapes, traced on a probability field: each cell's chance spread by
 * the Gaussian (so neighbouring cells merge), but never above the highest chance nearby, so a band
 * always means "at least this chance" and touching cells don't add up to more than either has.
 */
export function forecastShapes(cells: ForecastCell[], res: number): GeoJSON.FeatureCollection {
  const kept = cells.filter((c) => c.p >= FORECAST_LEVELS[0] * 0.5);
  if (!kept.length) return { type: 'FeatureCollection', features: [] };
  const step = res / UP;
  let minLon = Infinity, minLat = Infinity, maxLon = -Infinity, maxLat = -Infinity;
  for (const c of kept) {
    minLon = Math.min(minLon, c.lon); minLat = Math.min(minLat, c.lat);
    maxLon = Math.max(maxLon, c.lon + res); maxLat = Math.max(maxLat, c.lat + res);
  }
  minLon -= PAD * res; minLat -= PAD * res; maxLon += PAD * res; maxLat += PAD * res;
  const w = Math.ceil((maxLon - minLon) / step);
  const h = Math.ceil((maxLat - minLat) / step);
  const a = new Float32Array(w * h);
  const half = Math.floor(UP / 2);
  for (const c of kept) {
    const x = Math.min(w - 1, Math.max(0, Math.round((c.lon - minLon) / res) * UP + half));
    const y = Math.min(h - 1, Math.max(0, Math.round((maxLat - c.lat) / res) * UP - UP + half));
    a[y * w + x] = Math.max(a[y * w + x], c.p);
  }
  // highest chance within one cell (UP raster pixels) in each direction
  const r = UP;
  const mh = new Float32Array(w * h), m = new Float32Array(w * h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    let v = 0;
    for (let k = Math.max(0, x - r); k <= Math.min(w - 1, x + r); k++) v = Math.max(v, a[y * w + k]);
    mh[y * w + x] = v;
  }
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    let v = 0;
    for (let k = Math.max(0, y - r); k <= Math.min(h - 1, y + r); k++) v = Math.max(v, mh[k * w + x]);
    m[y * w + x] = v;
  }
  const radius = Math.ceil(3 * SIGMA);
  const kernel = Array.from({ length: radius + 1 }, (_, i) => Math.exp(-(i * i) / (2 * SIGMA * SIGMA)));
  const b = new Float32Array(w * h);
  blur1d(a, b, w, h, true, kernel);
  blur1d(b, a, w, h, false, kernel);
  for (let i = 0; i < a.length; i++) a[i] = Math.min(a[i], m[i]);

  const shift = half + 0.5 - UP / 2;
  const toLonLat = ([x, y]: number[]) => [+(minLon + (x - shift) * step).toFixed(5), +(maxLat - (y - shift) * step).toFixed(5)];
  const bands = contours().size([w, h]).thresholds(FORECAST_LEVELS)(Array.from(a));
  return {
    type: 'FeatureCollection',
    features: bands
      .filter((mm) => mm.coordinates.length)
      .map((mm) => ({
        type: 'Feature',
        properties: { band: FORECAST_LEVELS.indexOf(mm.value) },
        geometry: { type: 'MultiPolygon', coordinates: mm.coordinates.map((poly) => poly.map((ring) => smooth(ring).map(toLonLat))) },
      })),
  };
}
