import { useState, type FormEvent } from 'react';
import type { BBox } from '../api/types';
import { useBox } from '../hooks/useData';
import { useSelection } from '../store/useSelection';
import { REGION } from '../config/regions';

const ns = (v: number) => `${Math.abs(v).toFixed(1)}°${v < 0 ? 'S' : 'N'}`;
const ew = (v: number) => `${Math.abs(v).toFixed(1)}°${v < 0 ? 'W' : 'E'}`;
const fmtBox = (b: BBox) => `${ns(b.max_lat)}–${ns(b.min_lat)}, ${ew(b.min_lon)}–${ew(b.max_lon)}`;

export default function AreaCard() {
  const { aoi, drawing, setAoi, setDrawing } = useSelection();
  const { box, outside, clipped } = useBox();
  const [coordsOpen, setCoordsOpen] = useState(false);
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
    setErr('');
    setCoordsOpen(false);
    setAoi({ min_lat: la - dLat, max_lat: la + dLat, min_lon: lo - dLon, max_lon: lo + dLon });
  }

  const btn = 'rounded-lg px-3 py-1.5 text-sm transition-colors';
  return (
    <div className="absolute left-3 top-3 w-[min(19rem,calc(100%-1.5rem))] rounded-xl border border-line bg-page/85 p-3.5 backdrop-blur sm:left-4 sm:top-4">
      <div className="text-xs text-muted">Area</div>
      <div className="mt-0.5 text-sm font-medium text-ink">{aoi ? 'Custom area' : `All of ${REGION.name}`}</div>
      {aoi && box && <div className="tabular text-xs text-ink-2">{fmtBox(box)}</div>}
      {outside && <div className="mt-1 text-xs text-ember-soft">This area is outside {REGION.name}. We don’t have data there yet.</div>}
      {clipped && <div className="mt-1 text-xs text-muted">Trimmed to the part inside {REGION.name}.</div>}

      {drawing ? (
        <div className="mt-3 flex items-center justify-between gap-2">
          <span className="text-xs text-ink-2">Click and drag on the map</span>
          <button className={`${btn} text-muted hover:text-ink`} onClick={() => setDrawing(false)}>Cancel</button>
        </div>
      ) : (
        <div className="mt-3 flex flex-wrap gap-1.5">
          <button className={`${btn} bg-ember font-medium text-page hover:bg-ember-soft`} onClick={() => setDrawing(true)}>
            Draw area
          </button>
          <button className={`${btn} bg-raised text-ink-2 hover:text-ink`} onClick={() => setCoordsOpen((o) => !o)} aria-expanded={coordsOpen}>
            Coordinates
          </button>
          {aoi && (
            <button className={`${btn} text-muted hover:text-ink`} onClick={() => setAoi(null)}>Reset</button>
          )}
        </div>
      )}

      {coordsOpen && !drawing && (
        <form onSubmit={submit} className="mt-3 grid grid-cols-3 gap-1.5">
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
          <button type="submit" className={`${btn} col-span-3 bg-raised text-ink hover:bg-line`}>Show this area</button>
        </form>
      )}
    </div>
  );
}
