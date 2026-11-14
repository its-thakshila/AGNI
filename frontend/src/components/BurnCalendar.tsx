import { useMemo } from 'react';
import type { CalendarCell, Category, Climatology } from '../api/types';
import { isModelled } from '../api/types';
import { friendlyError } from '../api/client';
import { useAnomaly, useBox, useCalendar } from '../hooks/useData';
import { useSelection } from '../store/useSelection';
import { CATEGORIES, CATEGORY_COLOR, CATEGORY_LABEL, EMBER, INK, NO_DATA, ORIGIN_LABEL, pick } from '../lib/colors';
import { MONTHS, MONTHS_LONG, compact, fmt, percentText } from '../lib/stats';
import { REGION } from '../config/regions';
import EChart, { AXIS_FONT, TOOLTIP } from './EChart';
import Notices from './Notices';

const ROW = 22;

function categoryOf(v: number, c: Climatology): Category {
  if (v < c.p10) return 'well_below';
  if (v < c.p25) return 'below';
  if (v <= c.p75) return 'normal';
  if (v <= c.p90) return 'above';
  return 'well_above';
}

interface Drawn { value: [number, number, number]; cell: CalendarCell; color: string; selected: boolean; category: Category | null }

/** Overlay ink that stays visible on both dark and light cells. */
function overlayInk(hex: string, alpha: number): string {
  const n = parseInt(hex.slice(1), 16);
  const lum = (0.299 * (n >> 16) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255)) / 255;
  return lum > 0.45 ? `rgba(11,11,13,${alpha + 0.2})` : `rgba(240,239,236,${alpha})`;
}

type Api = { value: (i: number) => number; coord: (v: number[]) => number[]; size: (v: number[]) => number[] };

export default function BurnCalendar() {
  const { data, isFetching, error } = useCalendar();
  const anomaly = useAnomaly().data?.data;
  const { box, outside } = useBox();
  const { year, month, mode, setMode, setMonth, types, setTypes } = useSelection();

  const model = useMemo(() => {
    const d = data?.data;
    if (!d) return null;
    const years = [...new Set(d.cells.map((c) => c.year))].sort((a, b) => a - b);
    const p95 = Math.max(1, d.scale.p95);
    const drawn: Drawn[] = d.cells.map((cell) => {
      const clim = d.climatology[cell.month - 1];
      let category: Category | null = null;
      if (cell.count !== null) {
        if (cell.complete && clim.years_used) category = categoryOf(cell.count, clim);
        else if (anomaly && anomaly.month === `${cell.year}-${String(cell.month).padStart(2, '0')}`) category = anomaly.category;
      }
      const color =
        cell.count === null ? NO_DATA
        : mode === 'count' ? pick(EMBER, Math.sqrt(cell.count / p95))
        : category ? CATEGORY_COLOR[category] : NO_DATA;
      return { value: [cell.month - 1, years.indexOf(cell.year), cell.count ?? 0], cell, color, selected: cell.year === year && cell.month === month, category };
    });
    const totals = years.map((y) => d.cells.filter((c) => c.year === y).reduce((s, c) => s + (c.count ?? 0), 0));
    const yearIncomplete = years.map((y) => d.cells.filter((c) => c.year === y && c.complete).length < 12);
    const modelYears = new Set(years.filter((y) => {
      const cs = d.cells.filter((c) => c.year === y);
      return cs.filter((c) => isModelled(c.main_origin)).length > cs.length / 2;
    }));
    return { years, drawn, totals, yearIncomplete, modelYears, clim: d.climatology, p95 };
  }, [data, anomaly, mode, year, month]);

  const option = useMemo(() => {
    if (!model) return {};
    const { years, drawn, totals, yearIncomplete, modelYears, clim } = model;

    // One rounded cell per month with a 2px gap. Overlays: dashed inset = modelled,
    // one diagonal = some days missing, crossed = no data, faded = month in progress.
    const renderItem = (params: { dataIndex: number }, api: Api) => {
      const d = drawn[params.dataIndex];
      const [cx, cy] = api.coord([api.value(0), api.value(1)]);
      const [w, h] = api.size([1, 1]);
      const r = { x: cx - w / 2 + 1, y: cy - h / 2 + 1, width: w - 2, height: h - 2 };
      const c = d.cell;
      const kids: object[] = [
        { type: 'rect', shape: { ...r, r: 3 }, style: { fill: d.color, opacity: c.complete ? 1 : 0.55 } },
      ];
      const line = (x1: number, y1: number, x2: number, y2: number) =>
        kids.push({ type: 'line', shape: { x1, y1, x2, y2 }, style: { stroke: overlayInk(d.color, 0.5), lineWidth: 1 } });
      if (c.has_data === 'none') {
        line(r.x + 4, r.y + r.height - 4, r.x + r.width - 4, r.y + 4);
        line(r.x + 4, r.y + 4, r.x + r.width - 4, r.y + r.height - 4);
      } else if (c.has_data === 'partial' && c.complete) {
        line(r.x + 4, r.y + r.height - 4, r.x + r.width - 4, r.y + 4);
      }
      if (isModelled(c.main_origin))
        kids.push({ type: 'rect', shape: { x: r.x + 2.5, y: r.y + 2.5, width: r.width - 5, height: r.height - 5, r: 2 }, style: { fill: null, stroke: overlayInk(d.color, 0.45), lineWidth: 1, lineDash: [2, 2] } });
      if (d.selected)
        kids.push({ type: 'rect', shape: { ...r, r: 3 }, style: { fill: null, stroke: INK.primary, lineWidth: 2 } });
      return { type: 'group', children: kids };
    };

    return {
      animation: false,
      grid: [
        { left: 52, right: 96, top: 24, bottom: 8 },
        { right: 8, width: 80, top: 24, bottom: 8 },
      ],
      xAxis: [
        { type: 'category', data: MONTHS, position: 'top', gridIndex: 0, axisLine: { show: false }, axisTick: { show: false }, axisLabel: AXIS_FONT },
        { type: 'value', gridIndex: 1, show: false, max: (v: { max: number }) => v.max * 1.6 },
      ],
      yAxis: [
        {
          type: 'category', data: years, inverse: true, gridIndex: 0, axisLine: { show: false }, axisTick: { show: false },
          axisLabel: {
            fontFamily: 'Inter', fontSize: 11,
            formatter: (y: string) => (modelYears.has(+y) ? `{m|${y}}` : `{o|${y}}`),
            rich: { o: { color: INK.secondary }, m: { color: INK.muted } },
          },
        },
        { type: 'category', data: years, inverse: true, gridIndex: 1, show: false },
      ],
      tooltip: {
        ...TOOLTIP,
        formatter: (p: { seriesIndex: number; dataIndex: number }) => {
          if (p.seriesIndex === 1) return `<b>${years[p.dataIndex]}</b><br/>${fmt(totals[p.dataIndex])} detections${yearIncomplete[p.dataIndex] ? ' (year not complete)' : ''}`;
          const d = drawn[p.dataIndex];
          const c = d.cell;
          const head = `<b>${MONTHS_LONG[c.month - 1]} ${c.year}</b>${c.complete ? '' : ` <span style="color:${INK.muted}">(days 1–${c.days_with_data}, in progress)</span>`}`;
          if (c.count === null) return `${head}<br/><span style="color:${INK.muted}">No satellite data</span>`;
          const usual = clim[c.month - 1].median;
          return [
            head,
            `<span style="font-size:15px;font-weight:600">${fmt(c.count)}</span> detections`,
            c.complete && usual ? `${percentText((100 * c.count) / usual)} <span style="color:${INK.muted}">(usual ${compact(usual)})</span>` : '',
            d.category ? `<span style="color:${INK.secondary}">${CATEGORY_LABEL[d.category]}</span>` : '',
            `<span style="color:${INK.secondary}">${c.main_origin ? ORIGIN_LABEL[c.main_origin] : ''}${c.uncertainty ? ` · ±${Math.round(c.uncertainty * 100)}%` : ''}</span>`,
            c.has_data === 'partial' && c.complete ? `<span style="color:#f8a466">${c.days_total - c.days_with_data} days without data</span>` : '',
          ].filter(Boolean).join('<br/>');
        },
      },
      series: [
        { type: 'custom', data: drawn, xAxisIndex: 0, yAxisIndex: 0, cursor: 'pointer', encode: { x: 0, y: 1 }, renderItem },
        {
          type: 'bar', xAxisIndex: 1, yAxisIndex: 1, barWidth: 8,
          data: totals.map((v, i) => ({
            value: v,
            itemStyle: { color: years[i] === year ? INK.secondary : yearIncomplete[i] ? '#2a2a30' : '#3a3a40', borderRadius: [0, 4, 4, 0] },
          })),
          label: { show: true, position: 'right', color: INK.muted, fontSize: 10, fontFamily: 'Inter', formatter: (p: { value: number }) => compact(p.value) },
        },
      ],
    };
  }, [model, year]);

  const height = (model?.years.length ?? 27) * ROW + 32;
  const seg = (active: boolean) => `rounded-md px-3 py-1.5 text-sm transition-colors ${active ? 'bg-raised text-ink' : 'text-muted hover:text-ink'}`;
  const includeIndustry = types.includes(2);

  return (
    <section className="rounded-2xl border border-line bg-surface p-4 sm:p-5" aria-label="Burning activity calendar">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-base font-medium text-ink">Burning calendar</h2>
          <p className="text-sm text-muted">Hotspot detections per month since 2000. Click a month to see it on the map and below.</p>
        </div>
        <div className="flex rounded-lg border border-line p-0.5" role="group" aria-label="Colour the calendar by">
          <button className={seg(mode === 'count')} onClick={() => setMode('count')} aria-pressed={mode === 'count'}>Detections</button>
          <button className={seg(mode === 'anomaly')} onClick={() => setMode('anomaly')} aria-pressed={mode === 'anomaly'}>Compared with usual</button>
        </div>
      </div>

      <div className="relative mt-4 overflow-x-auto">
        {box && (
          <EChart
            className="min-w-[560px]"
            height={height}
            option={option}
            ariaLabel="Calendar of hotspot detections by year and month"
            onClick={(p) => {
              const d = (p.data as Drawn | undefined)?.cell;
              if (p.seriesIndex === 0 && d) setMonth(d.year, d.month);
            }}
          />
        )}
        {isFetching && <div className="absolute right-2 top-0 text-xs text-muted">Updating…</div>}
        {outside && <p className="py-10 text-center text-sm text-muted">Pick an area inside {REGION.name} to see its calendar.</p>}
        {error && <p className="py-4 text-sm text-ember-soft">{friendlyError(error)}</p>}
      </div>

      <div className="mt-4 flex flex-wrap items-start justify-between gap-x-6 gap-y-3 border-t border-line pt-4 text-xs text-muted">
        {mode === 'count' ? (
          <div className="flex items-center gap-2">
            <span>0</span>
            <span className="flex h-2.5 w-36 overflow-hidden rounded-full">
              {EMBER.map((c) => <span key={c} className="flex-1" style={{ background: c }} />)}
            </span>
            <span>{model ? `${compact(model.p95)}+ detections` : 'More'}</span>
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
            {CATEGORIES.map((c) => (
              <span key={c} className="inline-flex items-center gap-1.5">
                <span className="inline-block h-2.5 w-4 rounded-sm" style={{ background: CATEGORY_COLOR[c] }} />
                {CATEGORY_LABEL[c]}
              </span>
            ))}
          </div>
        )}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
          <span className="inline-flex items-center gap-1.5"><Swatch kind="model" />Rebuilt from MODIS</span>
          <span className="inline-flex items-center gap-1.5"><Swatch kind="partial" />Some days missing</span>
          <span className="inline-flex items-center gap-1.5"><Swatch kind="none" />No data</span>
          <span className="inline-flex items-center gap-1.5"><Swatch kind="progress" />In progress</span>
        </div>
      </div>
      <div className="mt-3">
        <label className="flex cursor-pointer items-center gap-1.5 text-xs text-ink-2">
          <input type="checkbox" className="accent-[#f2793a]" checked={includeIndustry} onChange={(e) => setTypes(e.target.checked ? [0, 2, 3] : [0])} />
          Include industrial heat sources (gas flares, factories)
        </label>
      </div>
      <Notices warnings={data?.warnings} />
    </section>
  );
}

function Swatch({ kind }: { kind: 'model' | 'partial' | 'none' | 'progress' }) {
  return (
    <svg width="16" height="12" viewBox="0 0 16 12" aria-hidden>
      <rect x="0.5" y="0.5" width="15" height="11" rx="2" fill={kind === 'none' ? NO_DATA : '#93391d'} opacity={kind === 'progress' ? 0.55 : 1} />
      {kind === 'model' && <rect x="2.5" y="2.5" width="11" height="7" rx="1.5" fill="none" stroke="rgba(240,239,236,0.45)" strokeDasharray="2 2" />}
      {(kind === 'partial' || kind === 'none') && <line x1="3" y1="9" x2="13" y2="3" stroke="rgba(240,239,236,0.55)" />}
      {kind === 'none' && <line x1="3" y1="3" x2="13" y2="9" stroke="rgba(240,239,236,0.55)" />}
    </svg>
  );
}
