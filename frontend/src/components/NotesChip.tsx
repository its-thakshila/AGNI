import type { Warning } from '../api/types';
import { useAnomaly, useCalendar, useDaily } from '../hooks/useData';
import { useSelection } from '../store/useSelection';
import Popover from './ui/Popover';

// Warnings the UI already shows in its own way (e.g. "days 1–13" in the headline).
const SHOWN_ELSEWHERE = new Set(['MONTH_IN_PROGRESS']);

/** One quiet line at the bottom of the panel; the backend's warnings open in a popover. */
export default function NotesChip() {
  const tab = useSelection((s) => s.tab);
  const calendar = useCalendar().data?.warnings ?? [];
  const anomaly = useAnomaly().data?.warnings ?? [];
  const daily = useDaily().data?.warnings ?? [];
  // Notes follow what's on screen: the whole record for Calendar/Trends, the selected month for Month.
  const all: Warning[] = tab === 'month' ? [...anomaly, ...daily] : calendar;
  const seen = new Set<string>();
  const list = all.filter((w) => !SHOWN_ELSEWHERE.has(w.code) && !seen.has(w.code) && seen.add(w.code));

  return (
    <div className="border-t border-line px-4 py-2">
      {list.length ? (
        <Popover
          up
          ariaLabel="Notes about this data"
          buttonClassName="flex items-center gap-1.5 text-xs text-muted transition-colors hover:text-ink"
          panelClassName="w-80"
          label={
            <>
              <svg width="13" height="13" viewBox="0 0 14 14" aria-hidden>
                <circle cx="7" cy="7" r="6" fill="none" stroke="currentColor" strokeWidth="1.2" />
                <path d="M7 6.2v3.6M7 4.2v.1" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
              </svg>
              {list.length} {list.length === 1 ? 'note' : 'notes'} about this data
            </>
          }
        >
          <ul className="space-y-1.5 text-xs leading-relaxed text-ink-2">
            {list.map((w) => (
              <li key={w.code} className="flex gap-2">
                <span aria-hidden className="mt-[6px] inline-block size-1.5 shrink-0 rounded-full bg-ember-soft/70" />
                <span>{w.message}</span>
              </li>
            ))}
          </ul>
        </Popover>
      ) : (
        <span className="text-xs text-muted/70">Counts are satellite detections, not individual fires.</span>
      )}
    </div>
  );
}
