import { ChevronLeft, ChevronRight, Pause, Play, X } from 'lucide-react';
import { useDayStep } from '../hooks/useMonthNav';
import { useSelection } from '../store/useSelection';
import Icon from './ui/Icon';
import SpeedButton from './SpeedButton';

const dayLabel = (y: number, m: number, d: number) =>
  new Date(Date.UTC(y, m - 1, d)).toLocaleDateString('en-AU', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });

/**
 * Shown on the map while it shows a single day (chosen in the Calendar tab's days view or the
 * Month tab's daily chart): ‹ › (or ← →) step day by day, on across month boundaries; × returns
 * to the whole month; ▶ plays through the month. The day's numbers are in the headline.
 */
export default function DayControl() {
  const { year, month, day, playing, setDay, setPlaying } = useSelection();
  const step = useDayStep();
  if (day === null) return null;

  const btn = 'grid size-f6 place-items-center rounded-full text-ink-2 transition-colors hover:bg-raised hover:text-ink disabled:opacity-30 disabled:hover:bg-transparent';
  return (
    <div className="flex items-center gap-f1 rounded-full border border-line bg-page/85 p-f1 backdrop-blur" role="group" aria-label="Day shown on the map">
      <button
        className={`${btn} ${playing ? 'text-ember' : ''}`}
        onClick={() => setPlaying(!playing)}
        aria-label={playing ? 'Pause' : 'Play through the month'}
        title={playing ? 'Pause' : 'Play through the month, day by day'}
      >
        <Icon icon={playing ? Pause : Play} size="sm" />
      </button>
      <SpeedButton className="grid h-f6 w-f6 place-items-center" />
      <button className={btn} onClick={() => step.go(-1)} disabled={!step.canPrev} aria-label="Previous day" title="Previous day (left arrow key)">
        <Icon icon={ChevronLeft} size="sm" />
      </button>
      <span className="chip tabular w-[9.75rem] shrink-0 overflow-hidden text-ellipsis whitespace-nowrap text-center text-body font-medium text-ink">{dayLabel(year, month, day)}</span>
      <button className={btn} onClick={() => step.go(1)} disabled={!step.canNext} aria-label="Next day" title="Next day (right arrow key)">
        <Icon icon={ChevronRight} size="sm" />
      </button>
      <button className={btn} onClick={() => setDay(null)} aria-label="Show the whole month" title="Whole month">
        <Icon icon={X} size="sm" />
      </button>
    </div>
  );
}
