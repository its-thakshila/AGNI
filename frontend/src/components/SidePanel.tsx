import { CalendarDays, ChartColumn, TrendingUp, type LucideIcon } from 'lucide-react';
import { useSelection, type Tab } from '../store/useSelection';
import Headline from './Headline';
import NotesChip from './NotesChip';
import SettingsMenu from './SettingsMenu';
import CalendarTab from './tabs/CalendarTab';
import MonthTab from './tabs/MonthTab';
import TrendsTab from './tabs/TrendsTab';
import Icon from './ui/Icon';

const TABS: { id: Tab; label: string; icon: LucideIcon }[] = [
  { id: 'calendar', label: 'Calendar', icon: CalendarDays },
  { id: 'month', label: 'Month', icon: ChartColumn },
  { id: 'trends', label: 'Trends', icon: TrendingUp },
];

// Side panel: 1/φ² ≈ 38.2% of the width (map gets the golden 61.8%), clamped for small and huge screens.
export default function SidePanel() {
  const { tab, setTab } = useSelection();
  return (
    <aside
      className="flex flex-col overflow-hidden rounded-f5 border border-line bg-surface lg:min-h-0 lg:w-[clamp(400px,calc((100%_-_13px)*0.382),560px)]"
      aria-label="Fire activity"
    >
      <Headline />
      <div className="flex items-center gap-f3 px-f5 pb-f4">
        <div role="tablist" aria-label="Views" className="grid flex-1 grid-cols-3 gap-f1 rounded-f4 bg-page/60 p-f1">
          {TABS.map((t) => (
            <button
              key={t.id}
              role="tab"
              id={`tab-${t.id}`}
              aria-selected={tab === t.id}
              aria-controls={`panel-${t.id}`}
              onClick={() => setTab(t.id)}
              className={`flex items-center justify-center gap-f2 rounded-f3 py-f2 text-body transition-colors ${
                tab === t.id ? 'bg-raised text-ink shadow-sm shadow-black/40' : 'text-muted hover:text-ink-2'
              }`}
            >
              <Icon icon={t.icon} size="sm" className={tab === t.id ? 'text-ember' : ''} />
              {t.label}
            </button>
          ))}
        </div>
        <SettingsMenu />
      </div>
      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} className="flex min-h-[460px] flex-1 flex-col px-f5 pb-f4 lg:min-h-0">
        {tab === 'calendar' && <CalendarTab />}
        {tab === 'month' && <MonthTab />}
        {tab === 'trends' && <TrendsTab />}
      </div>
      <NotesChip />
    </aside>
  );
}
