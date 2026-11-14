// Map backgrounds. The dark vector map (CARTO) always provides borders and place names; the other
// options put NASA imagery (GIBS, public domain, no key) underneath them instead of its land and
// water fills. Imagery is dimmed and slightly desaturated so the dark theme and the ember hotspots
// still read first.

import type { Map as MLMap, RasterLayerSpecification } from 'maplibre-gl';

export type Basemap = 'dark' | 'satellite' | 'daily' | 'terrain';

const GIBS = 'https://gibs.earthdata.nasa.gov/wmts/epsg3857/best';
const NASA = '<a href="https://earthdata.nasa.gov/gibs" target="_blank" rel="noopener">NASA EOSDIS GIBS</a>';

export const BASEMAPS: { id: Basemap; label: string; detail: string }[] = [
  { id: 'dark', label: 'Dark', detail: 'Plain map: hotspots stand out most' },
  { id: 'satellite', label: 'Satellite', detail: 'Cloud-free NASA Blue Marble' },
  { id: 'daily', label: 'Satellite on the day', detail: 'NASA true colour of the day shown: smoke, clouds, burn scars' },
  { id: 'terrain', label: 'Terrain', detail: 'Shaded relief: ranges and valleys' },
];

/** The daily true-colour layer: VIIRS (S-NPP) from late 2015, MODIS (Terra) before. */
function dailyLayer(date: string) {
  return date >= '2015-11-24' ? 'VIIRS_SNPP_CorrectedReflectance_TrueColor' : 'MODIS_Terra_CorrectedReflectance_TrueColor';
}

function imagery(kind: Exclude<Basemap, 'dark'>, date: string): { tiles: string; maxzoom: number; paint: RasterLayerSpecification['paint'] } {
  switch (kind) {
    case 'satellite':
      return {
        tiles: `${GIBS}/BlueMarble_NextGeneration/default/2004-08-01/GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg`,
        maxzoom: 8,
        paint: { 'raster-brightness-max': 0.72, 'raster-saturation': -0.15, 'raster-contrast': 0.08 },
      };
    case 'daily':
      return {
        tiles: `${GIBS}/${dailyLayer(date)}/default/${date}/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg`,
        maxzoom: 9,
        paint: { 'raster-brightness-max': 0.66, 'raster-saturation': -0.1, 'raster-contrast': 0.05 },
      };
    case 'terrain':
      return {
        tiles: `${GIBS}/ASTER_GDEM_Greyscale_Shaded_Relief/default/2000-03-01/GoogleMapsCompatible_Level12/{z}/{y}/{x}.jpg`,
        maxzoom: 12,
        // greyscale relief kept dark, like the plain map, with the ridges still visible
        paint: { 'raster-brightness-max': 0.42, 'raster-brightness-min': 0.04, 'raster-contrast': 0.15 },
      };
  }
}

// Dark-map layers hidden under imagery: land, water and buildings (fills) and the road network.
// Borders, coastlines and place names stay.
// (Terrain keeps the dark water: the relief covers land only, so the sea and coast stay crisp.)
const hiddenUnderImagery = (id: string, type: string, kind: Basemap) =>
  (type === 'fill' && !(kind === 'terrain' && id.startsWith('water'))) || /^(road|tunnel|bridge|rail|aeroway|waterway)/.test(id);

const available = new Map<string, Promise<boolean>>();
/**
 * Whether NASA has true-colour imagery for a day yet (the newest days arrive a few hours late, and
 * some days have none). One small tile over Australia is fetched to find out; answers are cached.
 */
export function dailyAvailable(date: string): Promise<boolean> {
  let r = available.get(date);
  if (!r) {
    r = fetch(`${GIBS}/${dailyLayer(date)}/default/${date}/GoogleMapsCompatible_Level9/2/2/3.jpg`)
      .then((res) => res.ok)
      .catch(() => false);
    available.set(date, r);
  }
  return r;
}

/** Show one background. `date` (YYYY-MM-DD) picks the day for the "Satellite on the day" option. */
export function applyBasemap(map: MLMap, kind: Basemap, date: string) {
  if (map.getLayer('imagery')) map.removeLayer('imagery');
  if (map.getSource('imagery')) map.removeSource('imagery');
  for (const l of map.getStyle().layers) {
    if (l.id.startsWith('grid-') || l.id.startsWith('aoi') || l.id === 'background') continue;
    const hide = kind !== 'dark' && hiddenUnderImagery(l.id, l.type, kind);
    if (hiddenUnderImagery(l.id, l.type, 'satellite')) map.setLayoutProperty(l.id, 'visibility', hide ? 'none' : 'visible');
  }
  if (kind === 'dark') return;
  const { tiles, maxzoom, paint } = imagery(kind, date);
  map.addSource('imagery', { type: 'raster', tiles: [tiles], tileSize: 256, maxzoom, attribution: NASA });
  // straight above the background, under everything else (hotspots, borders, labels)
  const above = map.getStyle().layers.find((l) => l.id !== 'background')?.id;
  map.addLayer({ id: 'imagery', type: 'raster', source: 'imagery', paint: { ...paint, 'raster-fade-duration': 150 } }, above);
}

/**
 * Borders and place names drawn above the hotspots (where they used to be hidden), and shown
 * from country-wide zooms: the dark map only starts state lines at zoom 6 and state names at 5.
 * Lines are a little brighter than the dark map's own and every label gets a dark halo, so states,
 * the coast and towns stay readable over burning areas and over imagery.
 */
export function styleBorders(map: MLMap) {
  const z = (stops: number[]) => ['interpolate', ['linear'], ['zoom'], ...stops] as never;
  if (map.getLayer('boundary_state')) {
    map.setFilter('boundary_state', ['==', ['get', 'kind_detail'], 4]);
    map.setLayerZoomRange('boundary_state', 3, 24);
    map.setPaintProperty('boundary_state', 'line-color', '#a3a2ad');
    map.setPaintProperty('boundary_state', 'line-width', z([3, 0.6, 6, 1.1, 10, 1.6]));
    map.setPaintProperty('boundary_state', 'line-opacity', 0.75);
    map.setPaintProperty('boundary_state', 'line-dasharray', [3, 2] as never);
  }
  if (map.getLayer('boundary_country_inner')) {
    map.setPaintProperty('boundary_country_inner', 'line-color', '#b9b8c2');
    map.setPaintProperty('boundary_country_inner', 'line-width', z([3, 0.8, 8, 1.4]));
  }
  if (map.getLayer('place_state')) {
    map.setLayerZoomRange('place_state', 3.5, 10);
    map.setLayoutProperty('place_state', 'text-size', z([3.5, 10, 5, 12, 7, 14]));
    map.setPaintProperty('place_state', 'text-color', '#a8a7b0');
  }
  for (const l of map.getStyle().layers) {
    if (l.type !== 'symbol') continue;
    map.setPaintProperty(l.id, 'text-halo-color', '#0b0b0d');
    map.setPaintProperty(l.id, 'text-halo-width', 1.2);
  }
}
