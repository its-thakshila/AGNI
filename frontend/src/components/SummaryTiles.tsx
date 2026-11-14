import { useMemo, type ReactNode } from 'react';
import { useAnomaly, useBox, useCalendar } from '../hooks/useData';
import { useSelection } from '../store/useSelection';
import { CATEGORY_COLOR, CATEGORY_LABEL, ORIGIN_LABEL } from '../lib/colors';
import { MONTHS, MONTHS_LONG, compact, fmt, ordinal, percentText } from '../lib/stats';

function Tile({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="rounded-xl border border-line bg-surface px-4 py-3.5">
      <div className="text-xs text-muted">{label}</div>
      <div className="tabular mt-1 text-2xl font-semibold tracking-tight text-ink">{value}</div>
      {sub && <div className="mt-0.5 text-xs text-ink-2">{sub}</div>}
    </div>
  );
}

export default function SummaryTiles() {
  const cal = useCalendar().data?.data;
  const an = useAnomaly().data?.data;
  const { year, month } = useSelection();
  const { outside } = useBox();

  const s = useMemo(() => {
    if (!cal) return null;
    const cell = cal.cells.find((c) => c.year === year && c.month === month);
    const full = cal.cells.filter((c) => c.complete && c.has_data === 'full' && c.count !== null);
    const peak = full.reduce((a, b) => (b.count! > a.count! ? b : a), full[0]);
    const years = new Map<number, { sum: number; n: number }>();
    for (const c of full) {
      const y = years.get(c.year) ?? { sum: 0, n: 0 };
      y.sum += c.count!;
      y.n++;
      years.set(c.year, y);
    }
    const [busyYear, busy] = [...years].filter(([, v]) => v.n === 12).reduce((a, b) => (b[1].sum > a[1].sum ? b : a), [0, { sum: 0, n: 0 }]);
    return { cell, peak, busyYear, busySum: busy.sum };
  }, [cal, year, month]);

  if (outside) return <div className="my-6" />;
  if (!s) return <div className="my-6 h-[92px] animate-pulse rounded-xl bg-surface" />;
  const mtd = an?.compared_days.month_to_date;
  const value = an && an.month === `${year}-${String(month).padStart(2, '0')}` ? an.value : s.cell?.count ?? null;

  return (
    <section className="my-6 grid grid-cols-2 gap-3 lg:grid-cols-4" aria-label="Summary">
      <Tile
        label={`${MONTHS_LONG[month - 1]} ${year}${mtd ? ` (1–${an!.compared_days.to_day})` : ''}`}
        value={value !== null ? fmt(value) : 'No data'}
        sub={value !== null ? `detections · ${s.cell?.main_origin ? ORIGIN_LABEL[s.cell.main_origin] : ''}` : 'Satellite data missing for this month'}
      />
      <Tile
        label={mtd ? 'Compared with the same days in other years' : `Compared with a usual ${MONTHS_LONG[month - 1]}`}
        value={an?.percent_of_median != null ? percentText(an.percent_of_median) : '—'}
        sub={
          an?.category ? (
            <span className="inline-flex items-center gap-1.5">
              <span className="inline-block size-2 rounded-full" style={{ background: CATEGORY_COLOR[an.category] }} />
              {CATEGORY_LABEL[an.category]}
              {an.rank ? ` · ${ordinal(an.rank)} busiest of ${an.rank_of} years` : ''}
            </span>
          ) : an ? `Usual: ${compact(an.baseline.median)}` : 'Loading…'
        }
      />
      <Tile label="Busiest month on record" value={s.peak ? `${MONTHS[s.peak.month - 1]} ${s.peak.year}` : '—'} sub={s.peak ? `${compact(s.peak.count!)} detections` : undefined} />
      <Tile label="Busiest year" value={s.busyYear ? String(s.busyYear) : '—'} sub={s.busyYear ? `${compact(s.busySum)} detections` : undefined} />
    </section>
  );
}
