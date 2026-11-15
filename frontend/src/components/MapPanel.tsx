import { useEffect, useMemo, useRef, useState } from 'react';
import maplibregl from '../lib/maplibre';
import type { GeoJSONSource, LngLat, Map as MLMap, MapMouseEvent, MapTouchEvent } from 'maplibre-gl';
import type { BBox } from '../api/types';
import { isModelled } from '../api/types';
import { friendlyError } from '../api/client';
import { ApiError } from '../api/errors';
import { useBox, useForecast, useForecastCells, useGrid, useMeta, useMonthQuery } from '../hooks/useData';
import { ForecastLegend } from './tabs/ForecastTab';
import { forecastShapes } from '../lib/hotspotShapes';
import MapControls from './MapControls';
import { applyBasemap, dailyAvailable, styleBorders } from '../lib/basemaps';
import { ymd } from '../lib/dates';
import DayControl from './DayControl';
import { FRAME_MS, usePlayback } from '../hooks/usePlayback';
import { useSelection } from '../store/useSelection';
import { EMBER, FORECAST_COLORS } from '../lib/colors';
import { shapesFor } from '../lib/shapeCache';
import AreaControl from './AreaControl';
import Icon from './ui/Icon';
import { CloudOff, LoaderCircle, RotateCw, TriangleAlert } from 'lucide-react';
import { useLive } from '../hooks/useLiveSync';

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

// One flat colour per contour level, from "some burning" to "intense". The first step is
// already clearly visible on the dark basemap; no blur, no outline, no glow.
export const BAND_COLORS = [EMBER[4], EMBER[6], EMBER[7], EMBER[8]];
const FILL_OPACITY = 0.95;
const FADE_MS = 300;
const BAND_FILL = ['match', ['get', 'band'], 0, BAND_COLORS[0], 1, BAND_COLORS[1], 2, BAND_COLORS[2], BAND_COLORS[3]];
const FORECAST_FILL = ['match', ['get', 'band'], 0, FORECAST_COLORS[0], 1, FORECAST_COLORS[1], 2, FORECAST_COLORS[2], FORECAST_COLORS[3]];

export default function MapPanel() {
  const el = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MLMap | null>(null);
  const [ready, setReady] = useState(false);
  const { aoi, drawing, setAoi, setDrawing } = useSelection();
  const { data: meta } = useMeta();
  const { box } = useBox();
  const grid = useGrid();
  const pending = useLive((s) => s.pending);
  const day = useSelection((s) => s.day);
  const playing = useSelection((s) => s.playing);
  usePlayback();
  // dim the map while a new period loads, so the old map is never mistaken for the new one;
  // shown only if loading takes more than a moment, to avoid a flash on quick updates
  // Forecast tab: the map shows the experimental forecast for the chosen day instead of the record
  const forecastMode = useSelection((s) => s.tab) === 'forecast';
  const forecast = useForecastCells(forecastMode);
  const forecastSummary = useForecast().data;
  const lead = useSelection((s) => s.lead);
  const loading = forecastMode
    ? forecast.isFetching && (forecast.isPlaceholderData || !forecast.data)
    : grid.isFetching && (grid.isPlaceholderData || !grid.data) && !playing;
  const [showLoading, setShowLoading] = useState(false);
  useEffect(() => {
    if (!loading) { setShowLoading(false); return; }
    const t = setTimeout(() => setShowLoading(true), 150);
    return () => clearTimeout(t);
  }, [loading]);
  const liveError = useLive((s) => s.error);

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
    // map credits start collapsed behind their "i" button (expanded they cover the legend on phones);
    // MapLibre opens them on its own while the map loads, so close them again until it has
    const collapseCredits = () => el.current?.querySelector('.maplibregl-ctrl-attrib')?.classList.remove('maplibregl-compact-show');
    collapseCredits();
    map.once('styledata', collapseCredits);
    map.once('idle', collapseCredits);
    map.on('load', () => {
      // hotspots go under the borders, coastline and place names, so regions stay identifiable
      const layers = map.getStyle().layers;
      const below = (layers.find((l) => l.id === 'boundary_county') ?? layers.find((l) => l.type === 'symbol'))?.id;
      // two identical layers: a new map fades in on the idle one while the shown one fades out,
      // so changing month or day (and playback) crossfades instead of flipping
      for (const id of ['grid-a', 'grid-b']) {
        map.addSource(id, { type: 'geojson', data: EMPTY });
        map.addLayer(
          {
            id, type: 'fill', source: id,
            // nested levels: draw the brighter, inner shapes on top
            layout: { 'fill-sort-key': ['get', 'band'] as never },
            paint: {
              'fill-color': BAND_FILL as never, 'fill-opacity': id === 'grid-a' ? FILL_OPACITY : 0, 'fill-antialias': true,
              'fill-opacity-transition': { duration: FADE_MS, delay: 0 },
            },
          },
          below,
        );
      }
      styleBorders(map);
      collapseCredits();
      map.addSource('aoi', { type: 'geojson', data: EMPTY });
      map.addLayer({ id: 'aoi-fill', type: 'fill', source: 'aoi', paint: { 'fill-color': '#f2793a', 'fill-opacity': 0.04 } });
      map.addLayer({ id: 'aoi-line', type: 'line', source: 'aoi', paint: { 'line-color': '#f2793a', 'line-width': 1.5 } });
      setReady(true);
    });
    mapRef.current = map;
    if (import.meta.env.DEV) (window as unknown as { __agniMap: MLMap }).__agniMap = map; // for debugging in the console
    // the panel layout can change the map's size without a window resize
    const ro = new ResizeObserver(() => map.resize());
    ro.observe(el.current!);
    return () => {
      ro.disconnect();
      map.remove();
    };
  }, []);

  // background: the dark map, or NASA imagery under the borders and labels
  const basemap = useSelection((s) => s.basemap);
  const setBasemap = useSelection((s) => s.setBasemap);
  const { year, month } = useSelection();
  // days of the period on screen still being fetched from NASA (the selected day, or the month);
  // fetching for other months doesn't cover the map you're looking at
  const period = day === null ? `${year}-${String(month).padStart(2, '0')}` : `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
  const fetching = pending.filter((d) => d.startsWith(period)).length;
  const monthQ = useMonthQuery();
  const shownDate = day !== null ? ymd(year, month, day) : monthQ?.end ?? '';
  // imagery for the day shown, but held still during playback (new tiles every frame would flicker)
  const [imageryDate, setImageryDate] = useState(shownDate);
  useEffect(() => { if (!playing && shownDate) setImageryDate(shownDate); }, [playing, shownDate]);
  // "on the day" falls back to the cloud-free imagery when NASA has nothing for that day (yet)
  const [dailyMissing, setDailyMissing] = useState(false);
  useEffect(() => {
    if (!ready || !imageryDate) return;
    let stale = false;
    (async () => {
      const missing = basemap === 'daily' && !(await dailyAvailable(imageryDate));
      if (stale) return;
      setDailyMissing(missing);
      applyBasemap(mapRef.current!, missing ? 'satellite' : basemap, imageryDate);
    })();
    return () => { stale = true; };
  }, [ready, basemap, basemap === 'daily' ? imageryDate : '']);

  const shapes = useMemo(() => {
    if (forecastMode) return forecast.data ? forecastShapes(forecast.data, 0.1) : EMPTY;
    const g = grid.data?.data;
    return g && box ? shapesFor(g) : EMPTY;
  }, [forecastMode, forecast.data, grid.data, box]);

  // violet bands for the forecast, ember for observed fire (set before the new shapes are shown)
  useEffect(() => {
    if (!ready) return;
    for (const id of ['grid-a', 'grid-b']) mapRef.current!.setPaintProperty(id, 'fill-color', (forecastMode ? FORECAST_FILL : BAND_FILL) as never);
  }, [ready, forecastMode]);

  const speed = useSelection((s) => s.speed);
  // the crossfade fits the frame: half a frame at normal speeds; at the top speed it lasts the whole
  // frame, so the map is always blending into the next day (continuous, no pause between days)
  useEffect(() => {
    if (!ready) return;
    const frame = FRAME_MS / speed;
    const duration = speed >= 8 ? Math.round(frame) : Math.min(FADE_MS, Math.round(frame * 0.5));
    for (const id of ['grid-a', 'grid-b']) mapRef.current!.setPaintProperty(id, 'fill-opacity-transition', { duration, delay: 0 });
  }, [ready, speed]);

  const shownLayer = useRef<'grid-a' | 'grid-b'>('grid-a');
  useEffect(() => {
    if (!ready) return;
    const map = mapRef.current!;
    const next = shownLayer.current === 'grid-a' ? 'grid-b' : 'grid-a';
    (map.getSource(next) as GeoJSONSource).setData(shapes);
    map.setPaintProperty(next, 'fill-opacity', FILL_OPACITY);
    map.setPaintProperty(shownLayer.current, 'fill-opacity', 0);
    shownLayer.current = next;
  }, [ready, shapes]);

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
    // one finger draws instead of panning; pinch-zoom is off too, so a two-finger slip can't move the map mid-draw
    map.dragPan.disable();
    map.touchZoomRotate.disable();
    // with panning off, MapLibre hands touch gestures back to the browser, so a drag would also
    // scroll the page. Claim every touch on the map while drawing (CSS for most browsers, and a
    // non-passive listener for older Safari).
    const surface = map.getCanvasContainer();
    surface.style.touchAction = 'none';
    const noScroll = (e: TouchEvent) => { if (e.cancelable) e.preventDefault(); };
    surface.addEventListener('touchmove', noScroll, { passive: false });
    let start: LngLat | null = null;
    let last: LngLat | null = null;
    const src = map.getSource('aoi') as GeoJSONSource;
    const toBox = (a: LngLat, b: LngLat): BBox => ({
      min_lat: Math.min(a.lat, b.lat), max_lat: Math.max(a.lat, b.lat), min_lon: Math.min(a.lng, b.lng), max_lon: Math.max(a.lng, b.lng),
    });
    const down = (e: MapMouseEvent) => { start = e.lngLat; };
    const move = (e: MapMouseEvent) => { if (start) src.setData({ type: 'FeatureCollection', features: [rect(toBox(start, e.lngLat))] }); };
    const finish = (end: LngLat) => {
      if (!start) return;
      const b = toBox(start, end);
      start = null;
      if (b.max_lat - b.min_lat > 0.05 && b.max_lon - b.min_lon > 0.05) setAoi(b);
      else setDrawing(false);
    };
    const up = (e: MapMouseEvent) => finish(e.lngLat);
    // phones send touch events, not mouse events: the same rectangle with one finger
    const touchDown = (e: MapTouchEvent) => {
      if (e.points.length !== 1) return;
      e.preventDefault();
      start = last = e.lngLat;
    };
    const touchMove = (e: MapTouchEvent) => {
      if (!start || e.points.length !== 1) return;
      e.preventDefault();
      last = e.lngLat;
      src.setData({ type: 'FeatureCollection', features: [rect(toBox(start, last))] });
    };
    const touchUp = () => { if (start && last) finish(last); };
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setDrawing(false);
    map.on('mousedown', down);
    map.on('mousemove', move);
    map.on('mouseup', up);
    map.on('touchstart', touchDown);
    map.on('touchmove', touchMove);
    map.on('touchend', touchUp);
    map.on('touchcancel', touchUp);
    window.addEventListener('keydown', esc);
    return () => {
      canvas.style.cursor = '';
      surface.style.touchAction = '';
      surface.removeEventListener('touchmove', noScroll);
      map.dragPan.enable();
      map.touchZoomRotate.enable();
      map.touchZoomRotate.disableRotation();
      map.off('mousedown', down);
      map.off('mousemove', move);
      map.off('mouseup', up);
      map.off('touchstart', touchDown);
      map.off('touchmove', touchMove);
      map.off('touchend', touchUp);
      map.off('touchcancel', touchUp);
      window.removeEventListener('keydown', esc);
    };
  }, [ready, drawing, setAoi, setDrawing]);

  const g = grid.data?.data;
  const modelShare = g && g.cells.length ? g.cells.filter((c) => isModelled(c.main_origin)).length / g.cells.length : 0;

  return (
    <section className="absolute inset-0" aria-label="Map of hotspot detections">
      {/* inline position: MapLibre's own CSS would override a utility class here */}
      <div ref={el} style={{ position: 'absolute', inset: 0 }} />
      <div className="absolute left-f4 top-f4 z-10">
        <AreaControl />
      </div>
      {/* the map couldn't load: same dimmed overlay, with the reason and a retry (loading wins while it retries) */}
      {!forecastMode && grid.error && !grid.isFetching && !(showLoading || fetching > 0) && (
        <div className="pointer-events-none absolute inset-0 z-[5] grid place-items-center bg-page/45 p-f4" role="alert">
          <div className="chip pointer-events-auto flex max-w-[22rem] items-center gap-f3 rounded-f5 border border-line bg-page/90 py-f2 pl-f4 pr-f2 text-body text-ink-2 shadow-lg backdrop-blur">
            <Icon icon={grid.error instanceof ApiError && grid.error.code === 'NETWORK' ? CloudOff : TriangleAlert} size="sm" className="shrink-0 text-ember-soft" />
            <span className="min-w-0">{friendlyError(grid.error)}</span>
            <button
              onClick={() => grid.refetch()}
              className="flex shrink-0 items-center gap-f1 rounded-full px-f3 py-f1 text-ink transition-colors hover:bg-raised"
            >
              <Icon icon={RotateCw} size="xs" />
              Retry
            </button>
          </div>
        </div>
      )}
      {(showLoading || (!forecastMode && fetching > 0)) && (
        <div className="pointer-events-none absolute inset-0 z-[5] grid place-items-center bg-page/45 transition-opacity" role="status" aria-live="polite">
          <div className="chip flex items-center gap-f3 rounded-f5 border border-line bg-page/90 px-f4 py-f2 text-body text-ink-2 shadow-lg backdrop-blur">
            <Icon icon={LoaderCircle} size="sm" className="shrink-0 animate-spin text-ember" />
            {!forecastMode && fetching > 0 ? (
              <span>
                <span className="block">Fetching new satellite data…</span>
                <span className="block text-cap text-muted">
                  {day === null ? `${fetching} ${fetching === 1 ? 'day' : 'days'} · ` : ''}usually 1–2 minutes
                </span>
              </span>
            ) : forecastMode ? 'Loading forecast…' : day === null ? 'Updating map…' : 'Loading day…'}
          </div>
        </div>
      )}
      {/* phones: one row below the area buttons, which take the top row */}
      <div className="absolute right-f4 top-[4.25rem] z-10 flex flex-col items-end gap-f2 sm:top-f4">
        {!forecastMode && <DayControl />}
      </div>
      {/* sits 5px above the 34px credits button (13px from the edge): one column, equal gaps */}
      <div className="absolute bottom-[52px] right-f4 z-10">
        <MapControls map={mapRef.current} basemap={basemap} onBasemap={setBasemap} dailyDate={imageryDate} dailyMissing={dailyMissing} />
      </div>
      {liveError && (
        <div className="absolute bottom-f4 right-f6 max-w-64 rounded-f4 border border-line bg-page/85 px-f4 py-f3 text-cap text-ember-soft backdrop-blur">{liveError}</div>
      )}
      <div className="pointer-events-none absolute bottom-f4 left-f4 max-w-[calc(100%-5.5rem)] rounded-f3 border border-line bg-page/80 px-f4 py-f3 backdrop-blur">
        {forecastMode ? (
          <>
            <ForecastLegend />
            <div className="mt-f1 text-cap text-[#c4a6ff]">
              Experimental forecast{forecastSummary?.leads[String(lead)] ? ` · ${new Date(`${forecastSummary.leads[String(lead)].target_date}T00:00:00Z`).toLocaleDateString('en-AU', { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC' })}` : ''}
            </div>
          </>
        ) : (
          <>
            <div className="flex items-center gap-f3 text-cap text-muted">
              <span>Some burning</span>
              <span className="flex gap-f1">
                {BAND_COLORS.map((c) => <span key={c} className="h-2 w-5 rounded-f2" style={{ background: c }} />)}
              </span>
              <span>Intense</span>
            </div>
            {modelShare > 0.5 && <div className="mt-f1 text-cap text-ink-2">Harmonized by Harmonic: where burning was, not exact points</div>}
          </>
        )}
      </div>
    </section>
  );
}
