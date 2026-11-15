import { Info } from 'lucide-react';
import type { Warning } from '../api/types';
import { useAnomaly, useCalendar, useDaily } from '../hooks/useData';
import { useSelection } from '../store/useSelection';
import Popover from './ui/Popover';
import Icon from './ui/Icon';
import { NOTE_ORDER } from '../api/engine';

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
  const rank = (w: Warning) => (NOTE_ORDER.includes(w.code) ? NOTE_ORDER.indexOf(w.code) : NOTE_ORDER.length);
  // one note per topic, in a fixed order (sources, then gaps, then how to read), whichever query sent it
  const list = all
    .filter((w) => !SHOWN_ELSEWHERE.has(w.code) && !seen.has(w.code) && seen.add(w.code))
    .sort((a, b) => rank(a) - rank(b));

  return (
    <div className="border-t border-line px-f5 py-f3">
      {list.length ? (
        <Popover
          up
          ariaLabel="Notes about this data"
          buttonClassName="flex items-center gap-f2 text-cap text-muted transition-colors hover:text-ink"
          panelClassName="w-80"
          label={
            <>
              <Icon icon={Info} size="xs" />
              {list.length} {list.length === 1 ? 'note' : 'notes'} about this data
            </>
          }
        >
          <ul className="space-y-f3 text-cap text-ink-2">
            {list.map((w) => (
              <li key={w.code} className="flex gap-f3">
                <span aria-hidden className="mt-[6px] inline-block size-f2 shrink-0 rounded-full bg-ember-soft/70" />
                <span>{w.message}</span>
              </li>
            ))}
          </ul>
        </Popover>
      ) : (
        <span className="text-cap text-muted/70">Counts are satellite detections, not individual fires.</span>
      )}
    </div>
  );
}
