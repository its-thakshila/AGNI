import { useEffect, useRef, useState } from 'react';
import maplibregl from '../lib/maplibre';
import type { GeoJSONSource, LngLat, Map as MLMap, MapMouseEvent } from 'maplibre-gl';
import type { BBox, GridCell } from '../api/types';
import { isModelled } from '../api/types';
import { friendlyError } from '../api/client';
import { useBox, useGrid, useMeta } from '../hooks/useData';
import { useSelection } from '../store/useSelection';
import { EMBER } from '../lib/colors';
import AreaControl from './AreaControl';

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
  const { aoi, drawing, setAoi, setDrawing } = useSelection();
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
    // the panel layout can change the map's size without a window resize
    const ro = new ResizeObserver(() => map.resize());
    ro.observe(el.current!);
    return () => {
      ro.disconnect();
      map.remove();
    };
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
    <section className="absolute inset-0" aria-label="Map of hotspot detections">
      {/* inline position: MapLibre's own CSS would override a utility class here */}
      <div ref={el} style={{ position: 'absolute', inset: 0 }} />
      <div className="absolute left-3 top-3 z-10">
        <AreaControl />
      </div>
      {grid.isFetching && (
        <div className="pointer-events-none absolute right-3 top-3 rounded-full border border-line bg-page/80 px-3 py-1 text-xs text-muted backdrop-blur">Loading…</div>
      )}
      {grid.error && (
        <div className="absolute right-3 top-3 max-w-64 rounded-lg border border-line bg-page/85 px-3 py-1.5 text-xs text-ember-soft backdrop-blur">{friendlyError(grid.error)}</div>
      )}
      <div className="pointer-events-none absolute bottom-3 left-3 rounded-lg border border-line bg-page/80 px-3 py-2 backdrop-blur">
        <div className="flex items-center gap-2 text-[11px] text-muted">
          <span>Few</span>
          <span className="flex h-1.5 w-24 overflow-hidden rounded-full">
            {ramp.map((c) => <span key={c} className="flex-1" style={{ background: c }} />)}
          </span>
          <span>Many detections per 10 km</span>
        </div>
        {modelShare > 0.5 && <div className="mt-1 text-[11px] text-ink-2">Rebuilt from MODIS: where burning was, not exact points</div>}
      </div>
    </section>
  );
}
