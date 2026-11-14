import { useSelection, type Tab } from '../store/useSelection';
import Headline from './Headline';
import NotesChip from './NotesChip';
import SettingsMenu from './SettingsMenu';
import CalendarTab from './tabs/CalendarTab';
import MonthTab from './tabs/MonthTab';
import TrendsTab from './tabs/TrendsTab';

const TABS: { id: Tab; label: string }[] = [
  { id: 'calendar', label: 'Calendar' },
  { id: 'month', label: 'Month' },
  { id: 'trends', label: 'Trends' },
];

export default function SidePanel() {
  const { tab, setTab } = useSelection();
  return (
    <aside className="flex flex-col border-line bg-surface max-lg:border-t lg:min-h-0 lg:w-[clamp(420px,36vw,560px)] lg:border-l" aria-label="Fire activity">
      <Headline />
      <div className="flex items-center justify-between gap-2 border-b border-line px-4">
        <div role="tablist" aria-label="Views" className="flex gap-1">
          {TABS.map((t) => (
            <button
              key={t.id}
              role="tab"
              id={`tab-${t.id}`}
              aria-selected={tab === t.id}
              aria-controls={`panel-${t.id}`}
              onClick={() => setTab(t.id)}
              className={`relative px-3 py-2.5 text-sm transition-colors ${tab === t.id ? 'text-ink' : 'text-muted hover:text-ink-2'}`}
            >
              {t.label}
              {tab === t.id && <span className="absolute inset-x-3 -bottom-px h-0.5 rounded-full bg-ember" />}
            </button>
          ))}
        </div>
        <SettingsMenu />
      </div>
      <div
        role="tabpanel"
        id={`panel-${tab}`}
        aria-labelledby={`tab-${tab}`}
        className="flex min-h-[460px] flex-1 flex-col px-4 pb-2 pt-3 lg:min-h-0"
      >
        {tab === 'calendar' && <CalendarTab />}
        {tab === 'month' && <MonthTab />}
        {tab === 'trends' && <TrendsTab />}
      </div>
      <NotesChip />
    </aside>
  );
}
