import { useState, type FormEvent } from 'react';
import { ChevronDown, Crosshair, Pencil, RotateCcw, X } from 'lucide-react';
import type { BBox } from '../api/types';
import { useBox } from '../hooks/useData';
import { useSelection } from '../store/useSelection';
import { REGION } from '../config/regions';
import Popover from './ui/Popover';
import Icon from './ui/Icon';

const ns = (v: number) => `${Math.abs(v).toFixed(1)}°${v < 0 ? 'S' : 'N'}`;
const ew = (v: number) => `${Math.abs(v).toFixed(1)}°${v < 0 ? 'W' : 'E'}`;
export const fmtBox = (b: BBox) => `${ns(b.max_lat)}–${ns(b.min_lat)}, ${ew(b.min_lon)}–${ew(b.max_lon)}`;

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
        <Popover
          label={
            <>
              <span className="text-ink">{aoi ? 'Custom area' : `All of ${REGION.name}`}</span>
              <Icon icon={ChevronDown} size="sm" className="text-muted" />
            </>
          }
          ariaLabel="Change area"
          buttonClassName={`flex items-center gap-f3 rounded-full px-f4 py-f3 text-body transition-colors hover:border-ink-2/40 ${glass}`}
          panelClassName="w-72"
        >
          {(close) => (
            <div className="space-y-f4">
              {aoi && box && <div className="tabular text-cap text-ink-2">{fmtBox(box)}</div>}
              <div>
                <div className="mb-f3 text-cap text-muted">Centre and radius</div>
                <CoordsForm onDone={close} />
              </div>
              {aoi && (
                <button
                  onClick={() => { setAoi(null); close(); }}
                  className="flex w-full items-center gap-f3 rounded-f3 px-f3 py-f3 text-body text-ink-2 transition-colors hover:bg-surface hover:text-ink"
                >
                  <Icon icon={RotateCcw} size="sm" />
                  Back to all of {REGION.name}
                </button>
              )}
            </div>
          )}
        </Popover>
        <button
          onClick={() => setDrawing(true)}
          className="flex items-center gap-f2 rounded-full bg-ember px-f4 py-f3 text-body font-medium text-page transition-colors hover:bg-ember-soft"
        >
          <Icon icon={Pencil} size="sm" strokeWidth={2.25} />
          Draw area
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
