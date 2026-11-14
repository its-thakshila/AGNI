import { useState, type FormEvent } from 'react';
import { ChevronDown, Crosshair, Pencil, X } from 'lucide-react';
import type { BBox } from '../api/types';
import { useBox } from '../hooks/useData';
import { useSelection } from '../store/useSelection';
import { REGION } from '../config/regions';
import Popover from './ui/Popover';
import Icon from './ui/Icon';

const ns = (v: number) => `${Math.abs(v).toFixed(1)}°${v < 0 ? 'S' : 'N'}`;
const ew = (v: number) => `${Math.abs(v).toFixed(1)}°${v < 0 ? 'W' : 'E'}`;
export const fmtBox = (b: BBox) => `${ns(b.max_lat)}–${ns(b.min_lat)}, ${ew(b.min_lon)}–${ew(b.max_lon)}`;

/** "420 × 350 km": the box's width (east–west, at its middle latitude) by height. */
export function boxSize(b: BBox): string {
  const midLat = ((b.min_lat + b.max_lat) / 2) * (Math.PI / 180);
  const w = (b.max_lon - b.min_lon) * 111.32 * Math.cos(midLat);
  const h = (b.max_lat - b.min_lat) * 110.57;
  const r = (km: number) => (km < 100 ? Math.max(1, Math.round(km)) : Math.round(km / 10) * 10).toLocaleString('en-AU');
  return `${r(w)} × ${r(h)} km`;
}

const field = 'tabular mt-f1 w-full rounded-f3 border border-line bg-surface px-f3 py-f2 text-body text-ink outline-none transition-colors focus:border-ember';

function CoordsForm({ onDone }: { onDone: () => void }) {
  const setAoi = useSelection((s) => s.setAoi);
  const [lat, setLat] = useState('');
  const [lon, setLon] = useState('');
  const [km, setKm] = useState('50');
  const [err, setErr] = useState('');

  function submit(e: FormEvent) {
    e.preventDefault();
    const la = +lat, lo = +lon, r = +km;
    if (!lat || !Number.isFinite(la) || la < -90 || la > 90) return setErr('Latitude must be between −90 and 90 (south is negative).');
    if (!lon || !Number.isFinite(lo) || lo < -180 || lo > 180) return setErr('Longitude must be between −180 and 180.');
    if (!(r > 0 && r <= 1000)) return setErr('Radius must be 1–1000 km.');
    const dLat = r / 111;
    const dLon = r / (111 * Math.cos((la * Math.PI) / 180));
    setAoi({ min_lat: la - dLat, max_lat: la + dLat, min_lon: lo - dLon, max_lon: lo + dLon });
    onDone();
  }

  return (
    <form onSubmit={submit} className="grid grid-cols-3 gap-f3">
      {([
        ['Latitude', lat, setLat, '-33.9'],
        ['Longitude', lon, setLon, '151.2'],
        ['Radius km', km, setKm, '50'],
      ] as const).map(([label, v, set, ph]) => (
        <label key={label} className="text-cap text-muted">
          {label}
          <input inputMode="decimal" value={v} placeholder={ph} onChange={(e) => set(e.target.value)} className={field} />
        </label>
      ))}
      {err && <p className="col-span-3 text-cap text-ember-soft">{err}</p>}
      <button type="submit" className="col-span-3 flex items-center justify-center gap-f2 rounded-f3 bg-surface px-f4 py-f3 text-body text-ink transition-colors hover:bg-line">
        <Icon icon={Crosshair} size="sm" />
        Show this area
      </button>
    </form>
  );
}

const glass = 'border border-line bg-page/85 backdrop-blur';

/** Top-left map control: area name, one-click Draw, and a menu with coordinates and reset. */
export default function AreaControl() {
  const { aoi, drawing, setAoi, setDrawing } = useSelection();
  const { box, outside, clipped } = useBox();

  if (drawing) {
    return (
      <div className={`chip flex items-center gap-f3 rounded-full py-f3 pl-f4 pr-f3 text-body text-ink-2 ${glass}`}>
        <span className="size-f3 animate-pulse rounded-full bg-ember" />
        Drag across the map to draw an area
        <button onClick={() => setDrawing(false)} aria-label="Cancel drawing" className="grid size-f5 place-items-center rounded-full text-muted hover:bg-raised hover:text-ink">
          <Icon icon={X} />
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-start gap-f3">
      <div className="flex items-center gap-f3">
        {/* with a drawn area, the chip carries its own × to clear it (back to the whole region) */}
        <div className={`flex items-center rounded-full ${glass} ${aoi ? 'pr-f1' : ''}`}>
        <Popover
          label={
            <>
              <span className="whitespace-nowrap text-ink">
                {aoi ? (
                  <>
                    {/* phones: the size alone (the × and the outline already say it's a drawn area) */}
                    <span className="hidden sm:inline">Custom area</span>
                    {box && <span className="tabular text-ink-2"><span className="hidden sm:inline"> · </span>{boxSize(box)}</span>}
                  </>
                ) : `All of ${REGION.name}`}
              </span>
              <Icon icon={ChevronDown} size="sm" className="text-muted" />
            </>
          }
          ariaLabel="Change area"
          buttonClassName="flex items-center gap-f3 rounded-full px-f4 py-f3 text-body transition-colors hover:text-ink"
          panelClassName="w-72"
        >
          {(close) => (
            <div className="space-y-f4">
              {aoi && box && <div className="tabular text-cap text-ink-2">{fmtBox(box)}</div>}
              <div>
                <div className="mb-f3 text-cap text-muted">Centre and radius</div>
                <CoordsForm onDone={close} />
              </div>
            </div>
          )}
        </Popover>
        {aoi && <span className="h-f5 w-px bg-line" aria-hidden />}
        {aoi && (
          <button
            onClick={() => setAoi(null)}
            aria-label={`Clear the area: back to all of ${REGION.name}`}
            title={`Clear: back to all of ${REGION.name}`}
            className="grid size-f6 place-items-center rounded-full text-muted transition-colors hover:bg-raised hover:text-ink"
          >
            <Icon icon={X} size="sm" />
          </button>
        )}
        </div>
        {/* orange for the first action; once an area is set, Redraw steps back so the area chip leads */}
        <button
          onClick={() => setDrawing(true)}
          className={`flex items-center gap-f2 whitespace-nowrap rounded-full px-f4 py-f3 text-body transition-colors ${
            aoi ? `${glass} text-ink-2 hover:text-ink` : 'bg-ember font-medium text-page hover:bg-ember-soft'
          }`}
        >
          <Icon icon={Pencil} size="sm" strokeWidth={2.25} />
          {aoi ? 'Redraw' : 'Draw area'}
        </button>
      </div>
      {(outside || clipped) && (
        <div className={`rounded-f4 px-f4 py-f3 text-cap ${glass}`}>
          {outside ? <span className="text-ember-soft">This area is outside {REGION.name}. No data there yet.</span> : <span className="text-muted">Trimmed to the part inside {REGION.name}.</span>}
        </div>
      )}
    </div>
  );
}
