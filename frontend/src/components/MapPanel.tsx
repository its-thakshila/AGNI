import { useEffect, useRef, useState } from 'react';
import maplibregl from '../lib/maplibre';
import type { GeoJSONSource, LngLat, Map as MLMap, MapMouseEvent } from 'maplibre-gl';
import type { BBox, GridCell } from '../api/types';
import { isModelled } from '../api/types';
import { friendlyError } from '../api/client';
import { useBox, useGrid, useMeta } from '../hooks/useData';
import { useSelection } from '../store/useSelection';
import { EMBER } from '../lib/colors';
import { MONTHS_LONG, compact } from '../lib/stats';
import AreaCard from './AreaCard';

const STYLE = 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json';
const EMPTY: GeoJSON.FeatureCollection = { type: 'FeatureCollection', features: [] };
const AUSTRALIA: [number, number, number, number] = [112, -44, 154, -9];

const toBounds = (b: BBox): [number, number, number, number] => [b.min_lon, b.min_lat, b.max_lon, b.max_lat];
const rect = (b: BBox): GeoJSON.Feature => ({
  type: 'Feature',
  properties: {},
  geometry: {
    type: 'Polygon',
    coordinates: [[[b.min_lon, b.min_lat], [b.max_lon, b.min_lat], [b.max_lon, b.max_lat], [b.min_lon, b.max_lat], [b.min_lon, b.min_lat]]],
  },
});

function gridToGeoJson(cells: GridCell[], max: number, res: number): GeoJSON.FeatureCollection {
  const lm = Math.log10(Math.max(1, max) + 1);
  return {
    type: 'FeatureCollection',
    features: cells.map((c) => ({
      type: 'Feature',
      properties: { t: Math.log10(c.count + 1) / lm, model: isModelled(c.main_origin) ? 1 : 0 },
      geometry: {
        type: 'Polygon',
        coordinates: [[[c.lon, c.lat], [c.lon + res, c.lat], [c.lon + res, c.lat + res], [c.lon, c.lat + res], [c.lon, c.lat]]],
      },
    })),
  };
}

// MapLibre "step" over EMBER, skipping the darkest steps so cells stay visible on the dark basemap.
const ramp = EMBER.slice(2);
const fillColor = ['step', ['get', 't'], ramp[0], ...ramp.slice(1).flatMap((c, i) => [(i + 1) / ramp.length, c])];

export default function MapPanel() {
  const el = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MLMap | null>(null);
  const [ready, setReady] = useState(false);
  const { aoi, drawing, setAoi, setDrawing, year, month } = useSelection();
  const { data: meta } = useMeta();
  const { box } = useBox();
  const grid = useGrid();

  useEffect(() => {
    const map = new maplibregl.Map({
      container: el.current!,
      style: STYLE,
      bounds: AUSTRALIA,
      fitBoundsOptions: { padding: 24 },
      attributionControl: { compact: true },
      dragRotate: false,
      pitchWithRotate: false,
    });
    map.touchZoomRotate.disableRotation();
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right');
    map.on('load', () => {
      const firstSymbol = map.getStyle().layers.find((l: { type: string }) => l.type === 'symbol')?.id;
      map.addSource('grid', { type: 'geojson', data: EMPTY });
      map.addLayer(
        { id: 'grid', type: 'fill', source: 'grid', paint: { 'fill-color': fillColor as never, 'fill-opacity': ['case', ['==', ['get', 'model'], 1], 0.7, 0.9] as never } },
        firstSymbol,
      );
      map.addSource('aoi', { type: 'geojson', data: EMPTY });
      map.addLayer({ id: 'aoi-fill', type: 'fill', source: 'aoi', paint: { 'fill-color': '#f2793a', 'fill-opacity': 0.04 } });
      map.addLayer({ id: 'aoi-line', type: 'line', source: 'aoi', paint: { 'line-color': '#f2793a', 'line-width': 1.5 } });
      setReady(true);
    });
    mapRef.current = map;
    return () => map.remove();
  }, []);

  useEffect(() => {
    if (!ready) return;
    const g = grid.data?.data;
    (mapRef.current!.getSource('grid') as GeoJSONSource).setData(g && box ? gridToGeoJson(g.cells, g.max_count, g.res) : EMPTY);
  }, [ready, grid.data, box]);

  // outline + fit to the queried box
  useEffect(() => {
    if (!ready) return;
    const map = mapRef.current!;
    (map.getSource('aoi') as GeoJSONSource).setData(aoi && box ? { type: 'FeatureCollection', features: [rect(box)] } : EMPTY);
    map.fitBounds(aoi && box ? toBounds(box) : meta ? toBounds(meta.coverage.bbox) : AUSTRALIA, { padding: 48, duration: 600 });
  }, [ready, aoi, box, meta]);

  // rectangle drawing: one request when the box is finished, none while dragging
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map || !drawing) return;
    const canvas = map.getCanvas();
    canvas.style.cursor = 'crosshair';
    map.dragPan.disable();
    let start: LngLat | null = null;
    const src = map.getSource('aoi') as GeoJSONSource;
    const toBox = (a: LngLat, b: LngLat): BBox => ({
      min_lat: Math.min(a.lat, b.lat), max_lat: Math.max(a.lat, b.lat), min_lon: Math.min(a.lng, b.lng), max_lon: Math.max(a.lng, b.lng),
    });
    const down = (e: MapMouseEvent) => { start = e.lngLat; };
    const move = (e: MapMouseEvent) => { if (start) src.setData({ type: 'FeatureCollection', features: [rect(toBox(start, e.lngLat))] }); };
    const up = (e: MapMouseEvent) => {
      if (!start) return;
      const b = toBox(start, e.lngLat);
      start = null;
      if (b.max_lat - b.min_lat > 0.05 && b.max_lon - b.min_lon > 0.05) setAoi(b);
      else setDrawing(false);
    };
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setDrawing(false);
    map.on('mousedown', down);
    map.on('mousemove', move);
    map.on('mouseup', up);
    window.addEventListener('keydown', esc);
    return () => {
      canvas.style.cursor = '';
      map.dragPan.enable();
      map.off('mousedown', down);
      map.off('mousemove', move);
      map.off('mouseup', up);
      window.removeEventListener('keydown', esc);
    };
  }, [ready, drawing, setAoi, setDrawing]);

  const g = grid.data?.data;
  const modelShare = g && g.cells.length ? g.cells.filter((c) => isModelled(c.main_origin)).length / g.cells.length : 0;

  return (
    <section className="relative h-[58vh] min-h-[380px] border-b border-line" aria-label="Map of hotspot detections">
      {/* inline position: MapLibre's own CSS would override a utility class here */}
      <div ref={el} style={{ position: 'absolute', inset: 0 }} />
      <AreaCard />
      <div className="pointer-events-none absolute right-3 top-3 rounded-xl border border-line bg-page/80 px-3.5 py-2.5 text-right backdrop-blur sm:right-4 sm:top-4">
        <div className="text-xs text-muted">Showing</div>
        <div className="text-sm font-medium text-ink">
          {MONTHS_LONG[month - 1]} {year}
        </div>
        <div className="tabular max-w-48 text-xs text-ink-2">
          {grid.isFetching ? 'Loading…' : grid.error ? friendlyError(grid.error) : g ? `${compact(g.total)} detections` : '—'}
        </div>
      </div>
      <div className="pointer-events-none absolute bottom-3 left-3 rounded-xl border border-line bg-page/80 px-3.5 py-2.5 backdrop-blur sm:bottom-4 sm:left-4">
        <div className="text-xs text-muted">Detections per 10 km square</div>
        <div className="mt-1.5 flex h-2 w-40 overflow-hidden rounded-full">
          {ramp.map((c) => <span key={c} className="flex-1" style={{ background: c }} />)}
        </div>
        <div className="mt-1 flex w-40 justify-between text-[10px] text-muted">
          <span>Few</span>
          <span>Many</span>
        </div>
        {modelShare > 0.5 && (
          <div className="mt-1.5 max-w-44 text-[11px] leading-snug text-ink-2">Rebuilt from MODIS: shows where burning was, not exact fire points.</div>
        )}
      </div>
    </section>
  );
}
