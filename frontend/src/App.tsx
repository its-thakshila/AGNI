import Header from './components/Header';
import MapPanel from './components/MapPanel';
import SidePanel from './components/SidePanel';
import { useMeta } from './hooks/useData';
import { friendlyError } from './api/client';

// Desktop: one screen, no page scroll. Map on the left, side panel on the right.
// Below 1024px: map on top, panel underneath, page scrolls.
export default function App() {
  const meta = useMeta();
  return (
    <div className="flex min-h-dvh flex-col lg:h-dvh">
      <Header />
      {meta.error && (
        <div className="border-b border-line bg-raised px-4 py-2 text-center text-sm text-ember-soft">{friendlyError(meta.error)}</div>
      )}
      <main className="flex flex-1 flex-col lg:min-h-0 lg:flex-row">
        <div className="relative h-[45vh] min-h-[320px] lg:h-auto lg:min-h-0 lg:flex-1">
          <MapPanel />
        </div>
        <SidePanel />
      </main>
    </div>
  );
}
