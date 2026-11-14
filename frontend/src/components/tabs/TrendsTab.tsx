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
    <div className="min-w-0 flex-1 rounded-lg bg-raised/60 px-3 py-2">
      <div className="truncate text-[11px] text-muted">{label}</div>
      <div className="tabular text-sm font-medium text-ink">{value}</div>
      {sub && <div className="tabular text-[11px] text-muted">{sub}</div>}
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
      return {
        year: y,
        total: cs.reduce((s, c) => s + (c.count ?? 0), 0),
        complete: cs.filter((c) => c.complete && c.has_data === 'full').length === 12,
        model: cs.filter((c) => isModelled(c.main_origin)).length > cs.length / 2,
      };
    });
    const full = rows.filter((r) => r.complete);
    const busiestYear = full.reduce((a, b) => (b.total > a.total ? b : a), full[0]);
    const quietestYear = full.reduce((a, b) => (b.total < a.total ? b : a), full[0]);
    const fullMonths = d.cells.filter((c) => c.complete && c.has_data === 'full' && c.count !== null);
    const peak = fullMonths.reduce((a, b) => (b.count! > a.count! ? b : a), fullMonths[0]);
    const med = d.climatology.map((c) => c.median);
    const topMonth = med.indexOf(Math.max(...med));
    // months still in progress are left off: a half-month would look like a sudden drop
    const thisYear = Array.from({ length: 12 }, (_, i) => {
      const c = d.cells.find((x) => x.year === year && x.month === i + 1);
      return c && c.complete ? c.count : null;
    });
    return { rows, busiestYear, quietestYear, peak, topMonth, thisYear };
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
          return `<b>${r.year}</b>${r.complete ? '' : ` <span style="color:${INK.muted}">(not complete)</span>`}<br/><span style="font-size:15px;font-weight:600">${fmt(r.total)}</span> detections${r.model ? `<br/><span style="color:${INK.secondary}">Rebuilt from MODIS (model)</span>` : ''}`;
        },
      },
      series: [{
        type: 'bar', barMaxWidth: 12, cursor: 'pointer',
        data: t.rows.map((r) => ({
          value: r.total,
          itemStyle: { color: r.year === year ? EMBER_LINE : r.model ? '#33333a' : '#4a4a52', opacity: r.complete ? 1 : 0.5, borderRadius: [3, 3, 0, 0] },
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
          return [
            `<b>${MONTHS_LONG[i]}</b>`,
            `Usual: ${fmt(c[i].median)} <span style="color:${INK.muted}">(most years ${compact(c[i].p25)}–${compact(c[i].p75)})</span>`,
            v !== null ? `<span style="color:${EMBER_LINE}">${year}: ${fmt(v)}</span>` : `<span style="color:${INK.muted}">${year}: no data yet</span>`,
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

  if (error) return <p className="py-4 text-sm text-ember-soft">{friendlyError(error)}</p>;
  if (!t) return <div className="flex-1 animate-pulse rounded-lg bg-raised/40" />;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      <div className="flex gap-2">
        <Stat label="Fire season peaks in" value={MONTHS_LONG[t.topMonth]} sub="usually" />
        <Stat label="Busiest month" value={t.peak ? `${MONTHS[t.peak.month - 1]} ${t.peak.year}` : '—'} sub={t.peak ? `${compact(t.peak.count!)}` : undefined} />
        <Stat label="Busiest year" value={t.busiestYear ? String(t.busiestYear.year) : '—'} sub={t.quietestYear ? `quietest ${t.quietestYear.year}` : undefined} />
      </div>

      <div className="flex min-h-[150px] flex-1 flex-col">
        <div className="flex items-baseline justify-between gap-2">
          <h3 className="text-sm font-medium text-ink">Every year</h3>
          <span className="text-[11px] text-muted">Darker bars: rebuilt from MODIS</span>
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

      <div className="flex min-h-[150px] flex-1 flex-col">
        <div className="flex items-baseline justify-between gap-2">
          <h3 className="text-sm font-medium text-ink">A typical year</h3>
          <span className="flex items-center gap-3 text-[11px] text-muted">
            <span className="flex items-center gap-1"><span className="inline-block h-0 w-3 border-t border-dashed border-ink-2" />Usual</span>
            <span className="flex items-center gap-1"><span className="inline-block h-0.5 w-3 rounded bg-[#f2803d]" />{year}</span>
          </span>
        </div>
        <div className="relative min-h-0 flex-1">
          <EChart option={seasonal} ariaLabel={`Usual detections by month, with ${year} for comparison`} />
        </div>
      </div>
    </div>
  );
}
