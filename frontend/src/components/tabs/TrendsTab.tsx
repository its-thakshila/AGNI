import { useMemo } from 'react';
import { friendlyError } from '../../api/client';
import { isModelled } from '../../api/types';
import { useCalendar } from '../../hooks/useData';
import { useSelection } from '../../store/useSelection';
import { INK } from '../../lib/colors';
import { MONTHS, MONTHS_LONG, compact, fmt } from '../../lib/stats';
import EChart, { AXIS_FONT, TOOLTIP } from '../EChart';

const EMBER_LINE = '#f2803d';
const axisY = { type: 'value', splitNumber: 3, axisLabel: { ...AXIS_FONT, formatter: (v: number) => compact(v) }, splitLine: { lineStyle: { color: '#222226' } } };

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="min-w-0 flex-1 rounded-f4 border border-line/70 bg-page/35 px-f4 py-f3">
      <div className="truncate text-cap text-muted">{label}</div>
      <div className="tabular text-body font-medium text-ink">{value}</div>
      {sub && <div className="tabular text-cap text-muted">{sub}</div>}
    </div>
  );
}

export default function TrendsTab() {
  const { data, error } = useCalendar();
  const { year, month, setMonth } = useSelection();
  const d = data?.data;

  const t = useMemo(() => {
    if (!d) return null;
    const years = [...new Set(d.cells.map((c) => c.year))].sort((a, b) => a - b);
    const rows = years.map((y) => {
      const cs = d.cells.filter((c) => c.year === y);
      const finished = cs.length === 12 && cs.every((c) => c.complete);
      return {
        year: y,
        total: cs.reduce((s, c) => s + (c.count ?? 0), 0),
        finished,
        firstMonth: Math.min(...cs.map((c) => c.month)),
        gaps: cs.some((c) => c.has_data !== 'full'),
        complete: finished && cs.every((c) => c.has_data === 'full'),
        model: cs.filter((c) => isModelled(c.main_origin)).length > cs.length / 2,
      };
    });
    // Records (highest) count every year and month, gaps included: missing satellite days can only
    // lower a total, so the highest one is the record either way (2001 has a MODIS outage but is
    // still the busiest year). The quietest year needs full coverage, or an outage would win it.
    const busiestYear = rows.reduce((a, b) => (b.total > a.total ? b : a), rows[0]);
    const full = rows.filter((r) => r.complete);
    const quietestYear = full.reduce((a, b) => (b.total < a.total ? b : a), full[0]);
    const months = d.cells.filter((c) => c.count !== null);
    const peak = months.reduce((a, b) => (b.count! > a.count! ? b : a), months[0]);
    const med = d.climatology.map((c) => c.median);
    const topMonth = med.indexOf(Math.max(...med));
    // months still in progress are left off: a half-month would look like a sudden drop
    const yearCells = Array.from({ length: 12 }, (_, i) => d.cells.find((x) => x.year === year && x.month === i + 1));
    const thisYear = yearCells.map((c) => (c && c.complete ? c.count : null));
    return { rows, busiestYear, quietestYear, peak, topMonth, thisYear, yearCells, firstYear: years[0], lastYear: years[years.length - 1] };
  }, [d, year]);

  const yearly = useMemo(() => {
    if (!t) return {};
    return {
      animation: false,
      grid: { left: 40, right: 8, top: 10, bottom: 22 },
      xAxis: { type: 'category', data: t.rows.map((r) => String(r.year)), axisLine: { lineStyle: { color: '#383835' } }, axisTick: { show: false }, axisLabel: { ...AXIS_FONT, interval: 'auto', hideOverlap: true } },
      yAxis: axisY,
      tooltip: {
        ...TOOLTIP,
        formatter: (p: { dataIndex: number }) => {
          const r = t.rows[p.dataIndex];
          const note = r.finished ? (r.gaps ? 'some days without satellite data' : '')
            : r.year === t.lastYear ? 'in progress'
            : r.year === t.firstYear ? `record starts in ${MONTHS_LONG[r.firstMonth - 1]}` : 'not complete';
          return `<b>${r.year}</b>${note ? ` <span style="color:${INK.muted}">(${note})</span>` : ''}<br/><span style="font-size:19px;font-weight:600">${fmt(r.total)}</span> detections${r.model ? `<br/><span style="color:${INK.secondary}">Harmonized from MODIS by Harmonic</span>` : ''}`;
        },
      },
      series: [{
        type: 'bar', barMaxWidth: 12, cursor: 'pointer',
        data: t.rows.map((r) => ({
          value: r.total,
          itemStyle: { color: r.year === year ? EMBER_LINE : r.model ? '#33333a' : '#4a4a52', opacity: r.finished ? 1 : 0.5, borderRadius: [3, 3, 0, 0] },
        })),
      }],
    };
  }, [t, year]);

  const seasonal = useMemo(() => {
    if (!d || !t) return {};
    const c = d.climatology;
    return {
      animation: false,
      grid: { left: 40, right: 8, top: 10, bottom: 22 },
      xAxis: { type: 'category', data: MONTHS, boundaryGap: false, axisLine: { lineStyle: { color: '#383835' } }, axisTick: { show: false }, axisLabel: AXIS_FONT },
      yAxis: axisY,
      tooltip: {
        ...TOOLTIP,
        trigger: 'axis',
        axisPointer: { type: 'line', lineStyle: { color: '#55545c' } },
        formatter: (ps: { dataIndex: number }[]) => {
          const i = ps[0].dataIndex;
          const v = t.thisYear[i];
          const cell = t.yearCells[i];
          // why there is no point for the selected year in this month
          const missing = !cell ? (year === t.firstYear ? 'before the record starts' : 'not yet')
            : !cell.complete ? 'month in progress' : 'no satellite data';
          return [
            `<b>${MONTHS_LONG[i]}</b>`,
            `Usual: ${fmt(c[i].median)} <span style="color:${INK.muted}">(usual range ${compact(c[i].p25)}–${compact(c[i].p75)})</span>`,
            v !== null
              ? `<span style="color:${EMBER_LINE}">${year}: ${fmt(v)}</span>${cell?.has_data === 'partial' ? ` <span style="color:#f8a466">(some days missing)</span>` : ''}`
              : `<span style="color:${INK.muted}">${year}: ${missing}</span>`,
          ].join('<br/>');
        },
      },
      series: [
        { type: 'line', stack: 'band', symbol: 'none', silent: true, lineStyle: { opacity: 0 }, data: c.map((x) => x.p25) },
        { type: 'line', stack: 'band', symbol: 'none', silent: true, lineStyle: { opacity: 0 }, areaStyle: { color: 'rgba(185,184,178,0.12)' }, data: c.map((x) => x.p75 - x.p25) },
        { type: 'line', symbol: 'none', lineStyle: { color: INK.secondary, width: 1.5, type: 'dashed' }, data: c.map((x) => x.median) },
        {
          type: 'line', symbol: 'circle', symbolSize: 6, showSymbol: true, connectNulls: false,
          lineStyle: { color: EMBER_LINE, width: 2 }, itemStyle: { color: EMBER_LINE, borderColor: '#131316', borderWidth: 2 },
          data: t.thisYear,
        },
      ],
    };
  }, [d, t, year]);

  if (error) return <p className="py-f5 text-body text-ember-soft">{friendlyError(error)}</p>;
  if (!t) return <div className="flex-1 animate-pulse rounded-f3 bg-raised/40" />;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-f4">
      <div className="flex gap-f3">
        {/* three different questions: the usual peak (typical year), the single record month, the record year */}
        <Stat label="Peak season" value={MONTHS_LONG[t.topMonth]} sub="in a typical year" />
        <Stat label="Record month" value={t.peak ? `${MONTHS[t.peak.month - 1]} ${t.peak.year}` : '—'} sub={t.peak ? `${compact(t.peak.count!)} detections` : undefined} />
        <Stat label="Record year" value={t.busiestYear ? String(t.busiestYear.year) : '—'} sub={t.quietestYear ? `quietest ${t.quietestYear.year}` : undefined} />
      </div>

      <div className="flex min-h-[150px] flex-1 flex-col rounded-f4 border border-line/70 bg-page/35 p-f4">
        <div className="flex items-baseline justify-between gap-f3">
          <h3 className="text-body font-medium text-ink">Every year</h3>
          <span className="text-cap text-muted">Darker bars: harmonized by Harmonic</span>
        </div>
        <div className="relative min-h-0 flex-1">
          <EChart
            option={yearly}
            ariaLabel="Total detections per year"
            onClick={(p) => {
              const r = t.rows[p.dataIndex];
              if (r) setMonth(r.year, month);
            }}
          />
        </div>
      </div>

      <div className="flex min-h-[150px] flex-1 flex-col rounded-f4 border border-line/70 bg-page/35 p-f4">
        <div className="flex items-baseline justify-between gap-f3">
          <h3 className="text-body font-medium text-ink">A typical year</h3>
          <span className="flex items-center gap-f4 text-cap text-muted">
            <span className="flex items-center gap-f1"><span className="inline-block h-0 w-3 border-t border-dashed border-ink-2" />Usual</span>
            <span className="hidden items-center gap-f1 sm:flex"><span className="inline-block h-2 w-3 rounded-[2px] bg-[rgba(185,184,178,0.22)]" />Usual range</span>
            <span className="flex items-center gap-f1"><span className="inline-block h-0.5 w-3 rounded bg-[#f2803d]" />{year}</span>
          </span>
        </div>
        <div className="relative min-h-0 flex-1">
          <EChart option={seasonal} ariaLabel={`Usual detections by month, with ${year} for comparison`} />
        </div>
      </div>
    </div>
  );
}
