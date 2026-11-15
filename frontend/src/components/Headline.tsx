import { Blend, ChevronLeft, ChevronRight, Satellite } from 'lucide-react';
import type { Origin } from '../api/types';
import { isModelled } from '../api/types';
import { useAnomaly, useBox, useCalendar, useDaily, useDayAnomaly } from '../hooks/useData';
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
  const { year, month, day, aoi } = useSelection();
  const { outside } = useBox();
  const nav = useMonthNav();
  const monthAnomaly = useAnomaly();
  const dayAnomaly = useDayAnomaly();
  // with a day selected (on the map), the numbers are that day's, compared with the same date in other years
  const anomaly = day === null ? monthAnomaly : dayAnomaly;
  const an = anomaly.data?.data;
  const cell = useCalendar().data?.data.cells.find((c) => c.year === year && c.month === month);
  const key = day === null ? `${year}-${String(month).padStart(2, '0')}` : `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
  const current = an && an.month === key ? an : undefined;
  // While the next day's (or month's) comparison loads, keep the previous one on screen, dimmed,
  // instead of blanking it: blanking changes the panel's height and makes everything below jump.
  const shown = current ?? (an && an.month.length === key.length ? an : undefined);
  const stale = !current && !!shown;
  // a day's count is already in the month's daily data, so the number itself updates at once
  const dailyDay = useDaily().data?.data.days.find((x) => x.fire_date === key);
  const value = current ? current.value
    : day === null ? cell?.count ?? null
    : dailyDay ? dailyDay.count : shown?.value ?? null;
  const mtd = current?.compared_days.month_to_date;
  const dayText = day === null ? null : new Date(Date.UTC(year, month - 1, day)).toLocaleDateString('en-AU', { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC' });
  const origin = day === null ? cell?.main_origin : (shown ? shown.history.find((h) => h.year === +shown.month.slice(0, 4))?.main_origin : null) ?? cell?.main_origin;

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
      </div>

      {outside ? (
        <p className="mt-f4 text-body text-muted">Choose an area inside {REGION.name} to see its fire history.</p>
      ) : (
        <div className="mt-f4 flex items-end justify-between gap-f4">
          {/* the number and its caption keep their width; on narrow screens the comparison wraps instead */}
          <div className="shrink-0">
            <div className="tabular text-display font-semibold text-ink">{value !== null && value !== undefined ? fmt(value) : '—'}</div>
            {/* two fixed lines: one wrapping line would change the height as the text beside it changes */}
            <div className="mt-f2 text-cap text-muted">
              <div className="truncate">detections{dayText ? ` · ${dayText}` : mtd ? ` · days 1–${current!.compared_days.to_day}` : ''}</div>
              <div className="truncate">{aoi ? 'custom area' : `all of ${REGION.name}`}</div>
            </div>
          </div>
          <div className={`min-w-0 text-right transition-opacity ${stale ? 'opacity-50' : ''}`}>
            {shown?.category && shown.percent_of_median !== null ? (
              <>
                <div className="flex items-center justify-end gap-f2 text-lead font-medium text-ink">
                  <span className="inline-block size-f3 rounded-full" style={{ background: CATEGORY_COLOR[shown.category] }} />
                  {percentText(shown.percent_of_median)}
                </div>
                <div className="mt-f1 text-cap text-muted">
                  {CATEGORY_LABEL[shown.category]}
                  {shown.rank ? ` · ${ordinal(shown.rank)} of ${shown.rank_of} years` : ''}
                </div>
              </>
            ) : anomaly.error && !anomaly.isFetching ? (
              <div className="max-w-48 text-cap text-ember-soft" title={friendlyError(anomaly.error)}>Comparison unavailable right now</div>
            ) : !shown ? (
              <div className="text-cap text-muted">Comparing…</div>
            ) : shown.value === null ? (
              <div className="text-cap text-muted">No satellite data this {day === null ? 'month' : 'day'}</div>
            ) : shown.has_data === 'partial' ? (
              <div className="max-w-48 text-cap text-muted">Some days have no satellite data, so no fair comparison</div>
            ) : (
              <div className="text-cap text-muted">Not enough other years to compare</div>
            )}
            {/* always there in day mode, whatever the state above, so the height never changes */}
            {day !== null && <div className="text-cap text-muted">vs {day} {MONTHS_LONG[month - 1].slice(0, 3)} in other years</div>}
          </div>
        </div>
      )}
      {!outside && origin && (
        <div className="mt-f4 border-t border-line pt-f3">
          <SourceLine origin={origin} />
        </div>
      )}
    </div>
  );
}
