import { useMemo, useState } from 'react';
import { downloadCsv, friendlyError } from '../../api/client';
import { isModelled } from '../../api/types';
import { useAnomaly, useDaily, useMeta, useMonthQuery } from '../../hooks/useData';
import { useSelection } from '../../store/useSelection';
import { INK, ORIGIN_LABEL } from '../../lib/colors';
import { boxAreaDeg2, daysBetween } from '../../lib/dates';
import { MONTHS_LONG, compact, fmt } from '../../lib/stats';
import EChart, { AXIS_FONT, TOOLTIP } from '../EChart';

const EMBER_LINE = '#f2803d';
const axisY = { type: 'value', splitNumber: 3, axisLabel: { ...AXIS_FONT, formatter: (v: number) => compact(v) }, splitLine: { lineStyle: { color: '#222226' } } };

function DailyChart() {
  const { data, error } = useDaily();
  const d = data?.data;

  const option = useMemo(() => {
    if (!d) return {};
    const days = d.days;
    const labels = days.map((x) => String(+x.fire_date.slice(8, 10)));
    const hasBand = days.some((x) => (x.uncertainty ?? 0) > 0);
    // no-data days as grey bands, so a gap never reads as "zero fires"
    const noData = days.flatMap((x, i) => (x.has_data === 'none' ? [[{ xAxis: labels[i] }, { xAxis: labels[i] }]] : []));
    return {
      animation: false,
      grid: { left: 40, right: 8, top: 10, bottom: 22 },
      xAxis: { type: 'category', data: labels, axisLine: { lineStyle: { color: '#383835' } }, axisTick: { show: false }, axisLabel: { ...AXIS_FONT, interval: 4 } },
      yAxis: axisY,
      tooltip: {
        ...TOOLTIP,
        trigger: 'axis',
        axisPointer: { type: 'line', lineStyle: { color: '#55545c' } },
        formatter: (ps: { dataIndex: number }[]) => {
          const x = days[ps[0].dataIndex];
          const head = `<b>${x.fire_date}</b>`;
          if (x.count === null) return `${head}<br/><span style="color:${INK.muted}">No satellite data</span>`;
          const origin = Object.entries(x.origin_mix).sort((a, b) => b[1]! - a[1]!)[0]?.[0] as keyof typeof ORIGIN_LABEL | undefined;
          return [
            head,
            `<span style="font-size:15px;font-weight:600">${fmt(x.count)}</span> detections${x.uncertainty ? ` <span style="color:${INK.muted}">±${Math.round(x.uncertainty * 100)}%</span>` : ''}`,
            x.daynight_reliable && x.count_day !== null ? `<span style="color:${INK.secondary}">Day ${fmt(x.count_day)} · Night ${fmt(x.count_night ?? 0)}</span>` : '',
            origin ? `<span style="color:${INK.secondary}">${ORIGIN_LABEL[origin]}</span>` : '',
          ].filter(Boolean).join('<br/>');
        },
      },
      series: [
        ...(hasBand
          ? [
              { type: 'line', stack: 'u', symbol: 'none', silent: true, lineStyle: { opacity: 0 }, data: days.map((x) => (x.count === null ? null : x.count * (1 - (x.uncertainty ?? 0)))) },
              { type: 'line', stack: 'u', symbol: 'none', silent: true, lineStyle: { opacity: 0 }, areaStyle: { color: 'rgba(242,128,61,0.16)' }, data: days.map((x) => (x.count === null ? null : 2 * x.count * (x.uncertainty ?? 0))) },
            ]
          : []),
        {
          type: 'line', symbol: 'circle', symbolSize: 4, showSymbol: false, connectNulls: false,
          lineStyle: { color: EMBER_LINE, width: 2 }, itemStyle: { color: EMBER_LINE },
          data: days.map((x) => x.count),
          markArea: noData.length ? { silent: true, itemStyle: { color: 'rgba(132,131,140,0.12)' }, data: noData } : undefined,
        },
      ],
    };
  }, [d]);

  return (
    <div className="flex min-h-[150px] flex-1 flex-col">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="text-sm font-medium text-ink">Day by day</h3>
        {d?.days.some((x) => (x.uncertainty ?? 0) > 0) && <span className="text-[11px] text-muted">Shaded: model uncertainty</span>}
      </div>
      <div className="relative min-h-0 flex-1">
        {error ? <p className="py-6 text-sm text-ember-soft">{friendlyError(error)}</p> : <EChart option={option} ariaLabel="Daily hotspot detections for the selected month" />}
      </div>
    </div>
  );
}

function HistoryChart() {
  const { data, error } = useAnomaly();
  const { year, month, setMonth } = useSelection();
  const a = data?.data;

  const option = useMemo(() => {
    if (!a) return {};
    const h = a.history;
    return {
      animation: false,
      grid: { left: 40, right: 8, top: 10, bottom: 22 },
      xAxis: { type: 'category', data: h.map((x) => String(x.year)), axisLine: { lineStyle: { color: '#383835' } }, axisTick: { show: false }, axisLabel: { ...AXIS_FONT, interval: 4 } },
      yAxis: axisY,
      tooltip: {
        ...TOOLTIP,
        trigger: 'item',
        formatter: (p: { dataIndex: number }) => {
          const x = h[p.dataIndex];
          if (x.value === null) return `<b>${x.year}</b><br/><span style="color:${INK.muted}">No data</span>`;
          return [
            `<b>${MONTHS_LONG[month - 1]} ${x.year}</b>${a.compared_days.month_to_date ? ` <span style="color:${INK.muted}">(days 1–${a.compared_days.to_day})</span>` : ''}`,
            `<span style="font-size:15px;font-weight:600">${fmt(x.value)}</span> detections`,
            x.main_origin ? `<span style="color:${INK.secondary}">${ORIGIN_LABEL[x.main_origin]}</span>` : '',
            x.has_data === 'partial' ? `<span style="color:#f8a466">Some days missing, left out of “usual”</span>` : '',
          ].filter(Boolean).join('<br/>');
        },
      },
      series: [
        {
          type: 'bar', barMaxWidth: 12, cursor: 'pointer',
          data: h.map((x) => ({
            value: x.value,
            itemStyle: { color: x.year === year ? EMBER_LINE : isModelled(x.main_origin) ? '#33333a' : '#4a4a52', borderRadius: [3, 3, 0, 0] },
          })),
          markLine: {
            silent: true, symbol: 'none',
            lineStyle: { color: INK.muted, type: 'dashed', width: 1 },
            label: { formatter: 'Usual', color: INK.muted, fontSize: 10, fontFamily: 'Inter', position: 'insideStartTop' },
            data: [{ yAxis: a.baseline.median }],
          },
        },
      ],
    };
  }, [a, year, month]);

  return (
    <div className="flex min-h-[150px] flex-1 flex-col">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="text-sm font-medium text-ink">{MONTHS_LONG[month - 1]} in every year</h3>
        {a?.compared_days.month_to_date && <span className="text-[11px] text-muted">Days 1–{a.compared_days.to_day} of each year</span>}
      </div>
      <div className="relative min-h-0 flex-1">
        {error ? (
          <p className="py-6 text-sm text-ember-soft">{friendlyError(error)}</p>
        ) : (
          <EChart
            option={option}
            ariaLabel={`Detections in ${MONTHS_LONG[month - 1]} for every year`}
            onClick={(p) => {
              const x = a?.history[p.dataIndex];
              if (x && x.value !== null) setMonth(x.year, month);
            }}
          />
        )}
      </div>
    </div>
  );
}

export default function MonthTab() {
  const { data: meta } = useMeta();
  const q = useMonthQuery();
  const daily = useDaily().data?.data;
  const [busy, setBusy] = useState(false);
  const [dlError, setDlError] = useState('');
  const tooBig = !!(q && meta && boxAreaDeg2(q.bbox) * daysBetween(q.start, q.end) > meta.limits.csv_max_deg2_days);

  async function download() {
    if (!q) return;
    setBusy(true);
    setDlError('');
    try {
      await downloadCsv(q);
    } catch (e) {
      setDlError(friendlyError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      <DailyChart />
      <HistoryChart />
      <div className="flex items-center justify-between gap-3 border-t border-line pt-3">
        <p className="text-xs text-muted">
          {dlError ? <span className="text-ember-soft">{dlError}</span>
            : tooBig ? 'Too much data for one file. Draw a smaller area to download.'
            : daily ? `Data on ${daily.totals.days_with_data} of ${daily.totals.days_total} days` : ''}
        </p>
        <button
          onClick={download}
          disabled={busy || !q || tooBig}
          className="shrink-0 rounded-lg border border-line px-3 py-1.5 text-sm text-ink-2 transition-colors hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
        >
          {busy ? 'Preparing…' : 'Download CSV'}
        </button>
      </div>
    </div>
  );
}
