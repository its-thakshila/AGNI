import { useEffect, useRef } from 'react';
import Header from './components/Header';
import MapPanel from './components/MapPanel';
import SidePanel from './components/SidePanel';
import { useMeta } from './hooks/useData';
import { useSelection } from './store/useSelection';
import { useLiveSync } from './hooks/useLiveSync';
import { friendlyError } from './api/client';

// Desktop: one screen, no page scroll. Two floating rounded panels split φ : 1
// (map 61.8%, side panel 38.2%) with 13px gutters. Below 1024px they stack and scroll.
export default function App() {
  const meta = useMeta();
  const latest = meta.data?.coverage.latest_fire_date;
  useLiveSync();

  // Open on the newest month that actually has data. Without recent FIRMS data the backend
  // can end at 2025-12-31, so "this month" from the browser clock would be empty.
  // Only on first load: later updates (new days arriving) must not move the user.
  const opened = useRef(false);
  useEffect(() => {
    if (!latest || opened.current) return;
    opened.current = true;
    const { year, month, setMonth } = useSelection.getState();
    const ly = +latest.slice(0, 4), lm = +latest.slice(5, 7);
    if (year * 12 + month > ly * 12 + lm) setMonth(ly, lm);
  }, [latest]);

  return (
    <div className="flex min-h-dvh flex-col lg:h-dvh">
      <Header />
      {meta.error && (
        <div className="mx-f4 mb-f4 rounded-f4 border border-line bg-raised px-f4 py-f3 text-center text-body text-ember-soft">{friendlyError(meta.error)}</div>
      )}
      <main className="flex flex-1 flex-col gap-f4 px-f4 pb-f4 lg:min-h-0 lg:flex-row">
        <div className="relative h-[45vh] min-h-[320px] overflow-hidden rounded-f5 border border-line lg:h-auto lg:min-h-0 lg:flex-1">
          <MapPanel />
        </div>
        <SidePanel />
      </main>
    </div>
  );
}
