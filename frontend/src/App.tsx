import Header from './components/Header';
import MapPanel from './components/MapPanel';
import SummaryTiles from './components/SummaryTiles';
import BurnCalendar from './components/BurnCalendar';
import MonthDetail from './components/MonthDetail';
import Footer from './components/Footer';
import { useMeta } from './hooks/useData';
import { friendlyError } from './api/client';

export default function App() {
  const meta = useMeta();
  return (
    <div className="min-h-screen">
      <Header />
      {meta.error && (
        <div className="border-b border-line bg-raised px-4 py-2 text-center text-sm text-ember-soft">{friendlyError(meta.error)}</div>
      )}
      <main>
        <MapPanel />
        <div className="mx-auto max-w-6xl px-4 sm:px-6">
          <SummaryTiles />
          <BurnCalendar />
          <MonthDetail />
        </div>
      </main>
      <Footer />
    </div>
  );
}
