import { ArrowRight, Blend, ChevronLeft, ChevronRight, Satellite } from 'lucide-react';
import type { Origin } from '../api/types';
import { isModelled } from '../api/types';
import { useAnomaly, useBox, useCalendar } from '../hooks/useData';
import { useMonthNav } from '../hooks/useMonthNav';
import { useSelection } from '../store/useSelection';
import { CATEGORY_COLOR, CATEGORY_LABEL, ORIGIN_DETAIL, ORIGIN_LABEL } from '../lib/colors';
import { MONTHS_LONG, fmt, ordinal, percentText } from '../lib/stats';
import { REGION } from '../config/regions';
import { friendlyError } from '../api/client';
import Icon from './ui/Icon';
import Popover from './ui/Popover';

/** Quiet "where does this number come from" line; the full explanation opens on demand. */
function SourceLine({ origin }: { origin: Origin }) {
  return (
    <Popover
      ariaLabel={`Data source: ${ORIGIN_LABEL[origin]}`}
      buttonClassName="flex items-center gap-f2 text-cap text-muted transition-colors hover:text-ink"
      panelClassName="w-72"
      label={
        <>
          <Icon icon={isModelled(origin) ? Blend : Satellite} size="xs" className={isModelled(origin) ? 'text-ember-soft' : ''} />
          <span className="underline decoration-line decoration-dotted underline-offset-4">{ORIGIN_LABEL[origin]}</span>
        </>
      }
    >
      <div className="text-body font-medium text-ink">{ORIGIN_LABEL[origin]}</div>
      <p className="mt-f2 text-cap text-ink-2">{ORIGIN_DETAIL[origin]}</p>
    </Popover>
  );
}

function NavButton({ dir, onClick, disabled }: { dir: 'prev' | 'next'; onClick: () => void; disabled: boolean }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      aria-label={dir === 'prev' ? 'Previous month' : 'Next month'}
      title={dir === 'prev' ? 'Previous month (left arrow key)' : 'Next month (right arrow key)'}
      className="grid size-f6 place-items-center rounded-full text-muted transition-colors hover:bg-raised hover:text-ink disabled:pointer-events-none disabled:opacity-30"
    >
      <Icon icon={dir === 'prev' ? ChevronLeft : ChevronRight} />
    </button>
  );
}

/** Always-visible answer for the selected month: how much burning, and is that unusual? */
export default function Headline() {
  const { year, month, aoi, tab, setTab } = useSelection();
  const { outside } = useBox();
  const nav = useMonthNav();
  const anomaly = useAnomaly();
  const an = anomaly.data?.data;
  const cell = useCalendar().data?.data.cells.find((c) => c.year === year && c.month === month);
  const current = an && an.month === `${year}-${String(month).padStart(2, '0')}` ? an : undefined;
  const value = current ? current.value : cell?.count ?? null;
  const mtd = current?.compared_days.month_to_date;

  return (
    <div className="px-f5 pb-f5 pt-f4" aria-live="polite">
      <div className="flex items-center justify-between gap-f3">
        <div className="-ml-f3 flex items-center">
          <NavButton dir="prev" onClick={nav.prev} disabled={!nav.canPrev} />
          <h1 className="min-w-[10rem] text-center text-lead font-medium text-ink">
            {MONTHS_LONG[month - 1]} {year}
          </h1>
          <NavButton dir="next" onClick={nav.next} disabled={!nav.canNext} />
        </div>
        {tab !== 'month' && !outside && (
          <button onClick={() => setTab('month')} className="flex items-center gap-f2 rounded-full px-f3 py-f1 text-cap text-muted transition-colors hover:bg-raised hover:text-ink">
            Month details
            <Icon icon={ArrowRight} size="xs" />
          </button>
        )}
      </div>

      {outside ? (
        <p className="mt-f4 text-body text-muted">Choose an area inside {REGION.name} to see its fire history.</p>
      ) : (
        <div className="mt-f4 flex items-end justify-between gap-f4">
          <div className="min-w-0">
            <div className="tabular text-display font-semibold text-ink">{value !== null && value !== undefined ? fmt(value) : '—'}</div>
            <div className="mt-f2 text-cap text-muted">
              detections{mtd ? ` · days 1–${current!.compared_days.to_day}` : ''} · {aoi ? 'custom area' : `all of ${REGION.name}`}
            </div>
          </div>
          <div className="shrink-0 text-right">
            {current?.category && current.percent_of_median !== null ? (
              <>
                <div className="flex items-center justify-end gap-f2 text-lead font-medium text-ink">
                  <span className="inline-block size-f3 rounded-full" style={{ background: CATEGORY_COLOR[current.category] }} />
                  {percentText(current.percent_of_median)}
                </div>
                <div className="mt-f1 text-cap text-muted">
                  {CATEGORY_LABEL[current.category]}
                  {current.rank ? ` · ${ordinal(current.rank)} of ${current.rank_of} years` : ''}
                </div>
              </>
            ) : anomaly.error && !anomaly.isFetching ? (
              <div className="max-w-48 text-cap text-ember-soft" title={friendlyError(anomaly.error)}>Comparison unavailable right now</div>
            ) : !current ? (
              <div className="text-cap text-muted">Comparing…</div>
            ) : value === null ? (
              <div className="text-cap text-muted">No satellite data this month</div>
            ) : (
              <div className="text-cap text-muted">Not enough other years to compare</div>
            )}
          </div>
        </div>
      )}
      {!outside && cell?.main_origin && (
        <div className="mt-f4 border-t border-line pt-f3">
          <SourceLine origin={cell.main_origin} />
        </div>
      )}
    </div>
  );
}
