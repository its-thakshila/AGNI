import { useAnomaly, useBox, useCalendar } from '../hooks/useData';
import { useMonthNav } from '../hooks/useMonthNav';
import { useSelection } from '../store/useSelection';
import { CATEGORY_COLOR, CATEGORY_LABEL, ORIGIN_LABEL } from '../lib/colors';
import { MONTHS_LONG, fmt, ordinal, percentText } from '../lib/stats';
import { REGION } from '../config/regions';

function Arrow({ dir, onClick, disabled }: { dir: 'prev' | 'next'; onClick: () => void; disabled: boolean }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      aria-label={dir === 'prev' ? 'Previous month' : 'Next month'}
      title={dir === 'prev' ? 'Previous month (←)' : 'Next month (→)'}
      className="grid size-7 place-items-center rounded-full text-muted transition-colors hover:bg-raised hover:text-ink disabled:pointer-events-none disabled:opacity-30"
    >
      <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden>
        <path d={dir === 'prev' ? 'M7.5 2.5 4 6l3.5 3.5' : 'M4.5 2.5 8 6 4.5 9.5'} fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      </svg>
    </button>
  );
}

/** Always-visible answer for the selected month: how much burning, and is that unusual? */
export default function Headline() {
  const { year, month, aoi, tab, setTab } = useSelection();
  const { outside } = useBox();
  const nav = useMonthNav();
  const an = useAnomaly().data?.data;
  const cell = useCalendar().data?.data.cells.find((c) => c.year === year && c.month === month);
  const current = an && an.month === `${year}-${String(month).padStart(2, '0')}` ? an : undefined;
  const value = current ? current.value : cell?.count ?? null;
  const mtd = current?.compared_days.month_to_date;

  return (
    <div className="border-b border-line px-4 pb-3.5 pt-3" aria-live="polite">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-0.5">
          <Arrow dir="prev" onClick={nav.prev} disabled={!nav.canPrev} />
          <h1 className="min-w-[9.5rem] text-center text-base font-medium text-ink">
            {MONTHS_LONG[month - 1]} {year}
          </h1>
          <Arrow dir="next" onClick={nav.next} disabled={!nav.canNext} />
        </div>
        {tab !== 'month' && !outside && (
          <button onClick={() => setTab('month')} className="text-xs text-muted transition-colors hover:text-ink">
            Month details →
          </button>
        )}
      </div>

      {outside ? (
        <p className="mt-3 text-sm text-muted">Choose an area inside {REGION.name} to see its fire history.</p>
      ) : (
        <div className="mt-2.5 flex items-end justify-between gap-4">
          <div>
            <div className="tabular text-3xl font-semibold tracking-tight text-ink">{value !== null && value !== undefined ? fmt(value) : '—'}</div>
            <div className="text-xs text-muted">
              detections{mtd ? ` · days 1–${current!.compared_days.to_day}` : ''} · {aoi ? 'custom area' : `all of ${REGION.name}`}
            </div>
          </div>
          <div className="text-right">
            {current?.category && current.percent_of_median !== null ? (
              <>
                <div className="flex items-center justify-end gap-1.5 text-sm font-medium text-ink">
                  <span className="inline-block size-2 rounded-full" style={{ background: CATEGORY_COLOR[current.category] }} />
                  {percentText(current.percent_of_median)}
                </div>
                <div className="text-xs text-muted">
                  {CATEGORY_LABEL[current.category]}
                  {current.rank ? ` · ${ordinal(current.rank)} of ${current.rank_of} years` : ''}
                </div>
              </>
            ) : value === null ? (
              <div className="text-xs text-muted">No satellite data this month</div>
            ) : (
              <div className="text-xs text-muted">Comparing…</div>
            )}
          </div>
        </div>
      )}
      {!outside && cell?.main_origin && <div className="mt-1.5 text-[11px] text-muted">{ORIGIN_LABEL[cell.main_origin]}</div>}
    </div>
  );
}
