import type { GridData } from '../api/types';
import { hotspotShapes } from './hotspotShapes';

// Shapes per map result, computed once. Playback computes the next day's shapes while the current
// day is still on screen, so showing it only swaps the layer (no 0.1–0.2 s of work at that moment).
const cache = new WeakMap<GridData, GeoJSON.FeatureCollection>();

export function shapesFor(g: GridData): GeoJSON.FeatureCollection {
  let s = cache.get(g);
  if (!s) {
    s = hotspotShapes(g.cells, g.max_count, g.res);
    cache.set(g, s);
  }
  return s;
}
