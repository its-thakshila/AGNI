import { ChevronLeft, Pause, Play } from 'lucide-react';
import type { DailyValue, Origin } from '../../api/types';
import { isModelled } from '../../api/types';
import { friendlyError } from '../../api/client';
import { useDaily, useMonthQuery } from '../../hooks/useData';
import { useSelection } from '../../store/useSelection';
import { EMBER, NO_DATA, ORIGIN_LABEL, pick } from '../../lib/colors';
import { daysInMonth } from '../../lib/dates';
import { MONTHS_LONG, compact, fmt } from '../../lib/stats';
import Icon from '../ui/Icon';
import SpeedButton from '../SpeedButton';
import { overlayInk } from './calendarInk';

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

const mainOrigin = (x: DailyValue) =>
  (Object.entries(x.origin_mix).sort((a, b) => (b[1] ?? 0) - (a[1] ?? 0))[0]?.[0] as Origin | undefined) ?? null;

/**
 * One month, day by day, in the same style as the month calendar: brighter = more detections
 * (relative to the month's busiest day). Click a day to show it on the map; click it again (or
 * "Whole month") to go back to the month on the map.
 */
export default function DaysView() {
  const { year, month, day, playing, setDay, setPlaying, setCalView } = useSelection();
  const q = useMonthQuery();
  const { data, error } = useDaily();
  const days = data?.data.days ?? [];
  const byDay = new Map(days.map((x) => [+x.fire_date.slice(8, 10), x]));
  const max = Math.max(1, ...days.map((x) => x.count ?? 0));
  const total = daysInMonth(year, month);
  const lead = (new Date(Date.UTC(year, month - 1, 1)).getUTCDay() + 6) % 7; // Monday first
  const rows = Math.ceil((lead + total) / 7);

  const back = () => {
    setDay(null);
    setCalView('months');
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center justify-between gap-f3">
        <button
          onClick={back}
          className="chip -ml-f2 flex shrink-0 items-center gap-f1 whitespace-nowrap rounded-full px-f2 py-f1 text-body text-ink-2 transition-colors hover:bg-raised hover:text-ink"
        >
          <Icon icon={ChevronLeft} size="sm" />
          All months
        </button>
        <div className="flex min-w-0 items-center gap-f2">
          {day !== null && (
            <button onClick={() => setDay(null)} className="chip whitespace-nowrap rounded-full border border-line px-f3 py-f1 text-cap text-ink-2 transition-colors hover:text-ink">
              Whole month
            </button>
          )}
          <button
            onClick={() => setPlaying(!playing)}
            className={`chip flex shrink-0 items-center gap-f1 whitespace-nowrap rounded-full border px-f3 py-f1 text-cap transition-colors ${playing ? 'border-ember/60 text-ember' : 'border-line text-ink-2 hover:text-ink'}`}
            title="Play the map through the month, day by day"
          >
            <Icon icon={playing ? Pause : Play} size="xs" />
            {playing ? 'Pause' : 'Play month'}
          </button>
          <SpeedButton className="border border-line px-f3 py-f1" />
        </div>
      </div>

      <div className="relative mt-f3 flex min-h-[280px] flex-1 flex-col">
        {error ? (
          <p className="py-f5 text-body text-ember-soft">{friendlyError(error)}</p>
        ) : !q || !data ? (
          <div className="absolute inset-0 animate-pulse rounded-f3 bg-raised/40" />
        ) : (
          <>
            <div className="grid grid-cols-7 gap-[2px] pb-f2 text-center text-cap text-muted">
              {WEEKDAYS.map((w) => <span key={w}>{w}</span>)}
            </div>
            <div className="grid min-h-0 flex-1 grid-cols-7 gap-[2px]" style={{ gridTemplateRows: `repeat(${rows}, minmax(0, 1fr))` }}>
              {Array.from({ length: lead }, (_, i) => <span key={`b${i}`} />)}
              {Array.from({ length: total }, (_, i) => {
                const d = i + 1;
                const x = byDay.get(d);
                if (!x) {
                  // outside the record (before it starts, or not happened / not processed yet)
                  return (
                    <span key={d} className="flex items-start justify-start rounded-[3px] border border-line/60 p-f1 text-cap text-muted/60">
                      {d}
                    </span>
                  );
                }
                const color = x.count === null ? NO_DATA : pick(EMBER, Math.sqrt(x.count / max));
                const ink = overlayInk(color, 0.85);
                const faint = overlayInk(color, 0.55);
                const origin = mainOrigin(x);
                const selected = d === day;
                const tip = x.count === null
                  ? `${d} ${MONTHS_LONG[month - 1]}: no satellite data`
                  : `${d} ${MONTHS_LONG[month - 1]}: ${fmt(x.count)} detections${origin ? ` · ${ORIGIN_LABEL[origin]}` : ''}`;
                return (
                  <button
                    key={d}
                    onClick={() => setDay(selected ? null : d)}
                    title={tip}
                    aria-label={tip}
                    aria-pressed={selected}
                    className="relative flex min-h-0 flex-col items-start justify-between overflow-hidden rounded-[3px] p-f1 text-left transition-[filter] hover:brightness-125"
                    style={{ background: color, boxShadow: selected ? `inset 0 0 0 2px #f0efec` : undefined }}
                  >
                    <span className="text-cap font-medium leading-none" style={{ color: ink }}>{d}</span>
                    {x.count !== null && x.count > 0 && (
                      <span className="hidden text-cap leading-none sm:block" style={{ color: faint }}>{compact(x.count)}</span>
                    )}
                    {x.count === null && (
                      <svg className="pointer-events-none absolute inset-0 size-full" preserveAspectRatio="none" viewBox="0 0 10 10" aria-hidden>
                        <line x1="1.5" y1="8.5" x2="8.5" y2="1.5" stroke={faint} strokeWidth="0.4" vectorEffect="non-scaling-stroke" />
                        <line x1="1.5" y1="1.5" x2="8.5" y2="8.5" stroke={faint} strokeWidth="0.4" vectorEffect="non-scaling-stroke" />
                      </svg>
                    )}
                    {isModelled(origin) && (
                      <span className="pointer-events-none absolute inset-[3px] rounded-[2px] border border-dashed" style={{ borderColor: faint }} aria-hidden />
                    )}
                  </button>
                );
              })}
            </div>
          </>
        )}
      </div>

      <div className="mt-f3 flex items-center gap-f2 text-cap text-muted">
        <span>0</span>
        <span className="flex h-2 w-24 overflow-hidden rounded-full">
          {EMBER.map((c) => <span key={c} className="flex-1" style={{ background: c }} />)}
        </span>
        <span>{data ? `${compact(max)} (busiest day)` : ''}</span>
      </div>
    </div>
  );
}
