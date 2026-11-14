import { useState, type FormEvent } from 'react';
import type { BBox } from '../api/types';
import { useBox } from '../hooks/useData';
import { useSelection } from '../store/useSelection';
import { REGION } from '../config/regions';
import Popover from './ui/Popover';

const ns = (v: number) => `${Math.abs(v).toFixed(1)}°${v < 0 ? 'S' : 'N'}`;
const ew = (v: number) => `${Math.abs(v).toFixed(1)}°${v < 0 ? 'W' : 'E'}`;
export const fmtBox = (b: BBox) => `${ns(b.max_lat)}–${ns(b.min_lat)}, ${ew(b.min_lon)}–${ew(b.max_lon)}`;

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
    <form onSubmit={submit} className="grid grid-cols-3 gap-1.5">
      {([
        ['Latitude', lat, setLat, '-33.9'],
        ['Longitude', lon, setLon, '151.2'],
        ['Radius km', km, setKm, '50'],
      ] as const).map(([label, v, set, ph]) => (
        <label key={label} className="text-[11px] text-muted">
          {label}
          <input
            inputMode="decimal"
            value={v}
            placeholder={ph}
            onChange={(e) => set(e.target.value)}
            className="tabular mt-0.5 w-full rounded-md border border-line bg-surface px-2 py-1.5 text-sm text-ink outline-none focus:border-ember"
          />
        </label>
      ))}
      {err && <p className="col-span-3 text-xs text-ember-soft">{err}</p>}
      <button type="submit" className="col-span-3 mt-1 rounded-lg bg-surface px-3 py-1.5 text-sm text-ink hover:bg-line">Show this area</button>
    </form>
  );
}

/** Top-left map control: area name, one-click Draw, and a menu with coordinates and reset. */
export default function AreaControl() {
  const { aoi, drawing, setAoi, setDrawing } = useSelection();
  const { box, outside, clipped } = useBox();

  const pill = 'flex items-center gap-2 rounded-full border border-line bg-page/85 px-3.5 py-1.5 text-sm backdrop-blur transition-colors';

  if (drawing) {
    return (
      <div className={`${pill} text-ink-2`}>
        <span className="size-2 animate-pulse rounded-full bg-ember" />
        Click and drag to draw an area
        <button onClick={() => setDrawing(false)} className="ml-1 text-muted hover:text-ink">Cancel</button>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-start gap-2">
      <div className="flex items-center gap-1.5">
        <Popover
          label={
            <>
              <span className="text-ink">{aoi ? 'Custom area' : `All of ${REGION.name}`}</span>
              <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden className="text-muted"><path d="M2 3.5 5 6.5 8 3.5" fill="none" stroke="currentColor" strokeWidth="1.4" /></svg>
            </>
          }
          ariaLabel="Change area"
          buttonClassName={`${pill} hover:border-ink-2/40`}
          panelClassName="w-72"
        >
          {(close) => (
            <div className="space-y-3">
              {aoi && box && <div className="tabular text-xs text-ink-2">{fmtBox(box)}</div>}
              <div>
                <div className="mb-1.5 text-xs text-muted">Centre and radius</div>
                <CoordsForm onDone={close} />
              </div>
              {aoi && (
                <button onClick={() => { setAoi(null); close(); }} className="w-full rounded-lg px-3 py-1.5 text-left text-sm text-ink-2 hover:bg-surface hover:text-ink">
                  Back to all of {REGION.name}
                </button>
              )}
            </div>
          )}
        </Popover>
        <button onClick={() => setDrawing(true)} className="rounded-full bg-ember px-3.5 py-1.5 text-sm font-medium text-page transition-colors hover:bg-ember-soft">
          Draw area
        </button>
      </div>
      {(outside || clipped) && (
        <div className="rounded-lg border border-line bg-page/85 px-3 py-1.5 text-xs backdrop-blur">
          {outside ? <span className="text-ember-soft">This area is outside {REGION.name}. No data there yet.</span> : <span className="text-muted">Trimmed to the part inside {REGION.name}.</span>}
        </div>
      )}
    </div>
  );
}
