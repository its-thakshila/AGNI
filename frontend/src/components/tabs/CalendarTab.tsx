import { useMemo } from 'react';
import type { CalendarCell, Category, Climatology } from '../../api/types';
import { fairCategory, isModelled } from '../../api/types';
import { friendlyError } from '../../api/client';
import { useAnomaly, useBox, useCalendar } from '../../hooks/useData';
import { useSelection } from '../../store/useSelection';
import { CATEGORIES, CATEGORY_COLOR, CATEGORY_LABEL, EMBER, INK, NO_DATA, ORIGIN_LABEL, pick } from '../../lib/colors';
import { MONTHS, MONTHS_LONG, compact, fmt, percentText } from '../../lib/stats';
import EChart, { AXIS_FONT, TOOLTIP } from '../EChart';
import { CalendarDays, CircleHelp } from 'lucide-react';
import Popover from '../ui/Popover';
import Icon from '../ui/Icon';
import DaysView from './DaysView';
import { overlayInk } from './calendarInk';

function categoryOf(v: number, c: Climatology): Category {
  if (v < c.p10) return 'well_below';
  if (v < c.p25) return 'below';
  if (v <= c.p75) return 'normal';
  if (v <= c.p90) return 'above';
  return 'well_above';
}

interface Drawn { value: [number, number, number]; cell: CalendarCell; color: string; selected: boolean; category: Category | null }
type Api = { value: (i: number) => number; coord: (v: number[]) => number[]; size: (v: number[]) => number[] };

export default function CalendarTab() {
  const calView = useSelection((s) => s.calView);
  return calView === 'days' ? <DaysView /> : <MonthsView />;
}

function MonthsView() {
  const { data, error } = useCalendar();
  const anomaly = useAnomaly().data?.data;
  const { box } = useBox();
  const { year, month, mode, setMode, setMonth, setCalView } = useSelection();

  const model = useMemo(() => {
    const d = data?.data;
    if (!d) return null;
    const years = [...new Set(d.cells.map((c) => c.year))].sort((a, b) => a - b);
    const p95 = Math.max(1, d.scale.p95);
    const drawn: Drawn[] = d.cells.map((cell) => {
      const clim = d.climatology[cell.month - 1];
      let category: Category | null = null;
      if (cell.count !== null) {
        if (cell.complete && clim.years_used) category = fairCategory(categoryOf(cell.count, clim), cell.has_data);
        else if (anomaly && anomaly.month === `${cell.year}-${String(cell.month).padStart(2, '0')}`) category = anomaly.category;
      }
      const color =
        cell.count === null ? NO_DATA
        : mode === 'count' ? pick(EMBER, Math.sqrt(cell.count / p95))
        : category ? CATEGORY_COLOR[category] : NO_DATA;
      return { value: [cell.month - 1, years.indexOf(cell.year), cell.count ?? 0], cell, color, selected: cell.year === year && cell.month === month, category };
    });
    const modelYears = new Set(years.filter((y) => {
      const cs = d.cells.filter((c) => c.year === y);
      return cs.filter((c) => isModelled(c.main_origin)).length > cs.length / 2;
    }));
    return { years, drawn, modelYears, clim: d.climatology, p95 };
  }, [data, anomaly, mode, year, month]);

  const option = useMemo(() => {
    if (!model) return {};
    const { years, drawn, modelYears, clim } = model;

    // One rounded cell per month. Overlays: dashed inset = modelled, one diagonal =
    // some days missing, cross = no data, faded = month in progress, white ring = selected.
    const renderItem = (params: { dataIndex: number }, api: Api) => {
      const d = drawn[params.dataIndex];
      const [cx, cy] = api.coord([api.value(0), api.value(1)]);
      const [w, h] = api.size([1, 1]);
      const gap = h > 16 ? 2 : 1.5;
      const r = { x: cx - w / 2 + gap / 2, y: cy - h / 2 + gap / 2, width: w - gap, height: h - gap };
      const c = d.cell;
      const pad = Math.min(4, r.height / 4);
      const kids: object[] = [{ type: 'rect', shape: { ...r, r: 3 }, style: { fill: d.color, opacity: c.complete ? 1 : 0.55 } }];
      const line = (x1: number, y1: number, x2: number, y2: number) =>
        kids.push({ type: 'line', shape: { x1, y1, x2, y2 }, style: { stroke: overlayInk(d.color, 0.5), lineWidth: 1 } });
      if (c.has_data === 'none') {
        line(r.x + pad, r.y + r.height - pad, r.x + r.width - pad, r.y + pad);
        line(r.x + pad, r.y + pad, r.x + r.width - pad, r.y + r.height - pad);
      } else if (c.has_data === 'partial' && c.complete) {
        line(r.x + pad, r.y + r.height - pad, r.x + r.width - pad, r.y + pad);
      }
      if (isModelled(c.main_origin) && r.height > 10)
        kids.push({ type: 'rect', shape: { x: r.x + 2.5, y: r.y + 2.5, width: r.width - 5, height: r.height - 5, r: 2 }, style: { fill: null, stroke: overlayInk(d.color, 0.45), lineWidth: 1, lineDash: [2, 2] } });
      if (d.selected) kids.push({ type: 'rect', shape: { ...r, r: 3 }, style: { fill: null, stroke: INK.primary, lineWidth: 2 } });
      return { type: 'group', children: kids };
    };

    return {
      animation: false,
      grid: { left: 38, right: 2, top: 20, bottom: 2 },
      xAxis: { type: 'category', data: MONTHS.map((m) => m[0] + m.slice(1, 3)), position: 'top', axisLine: { show: false }, axisTick: { show: false }, axisLabel: { ...AXIS_FONT, fontSize: 12, margin: 6 } },
      yAxis: {
        type: 'category', data: years, inverse: true, axisLine: { show: false }, axisTick: { show: false },
        axisLabel: {
          fontFamily: 'Inter', fontSize: 12, margin: 8,
          formatter: (y: string) => (modelYears.has(+y) ? `{m|${y}}` : `{o|${y}}`),
          rich: { o: { color: INK.secondary }, m: { color: INK.muted } },
        },
      },
      tooltip: {
        ...TOOLTIP,
        formatter: (p: { dataIndex: number }) => {
          const d = drawn[p.dataIndex];
          const c = d.cell;
          const head = `<b>${MONTHS_LONG[c.month - 1]} ${c.year}</b>${c.complete ? '' : ` <span style="color:${INK.muted}">(days 1–${c.days_with_data}, in progress)</span>`}`;
          if (c.count === null) return `${head}<br/><span style="color:${INK.muted}">No satellite data</span>`;
          const usual = clim[c.month - 1].median;
          return [
            head,
            `<span style="font-size:19px;font-weight:600">${fmt(c.count)}</span> detections`,
            c.complete && usual && (c.has_data === 'full' || d.category) ? `${percentText((100 * c.count) / usual)} <span style="color:${INK.muted}">(usual ${compact(usual)})</span>` : '',
            d.category ? `<span style="color:${INK.secondary}">${CATEGORY_LABEL[d.category]}</span>` : '',
            `<span style="color:${INK.secondary}">${c.main_origin ? ORIGIN_LABEL[c.main_origin] : ''}${c.uncertainty ? ` · ±${Math.round(c.uncertainty * 100)}%` : ''}</span>`,
            c.has_data === 'partial' && c.complete ? `<span style="color:#f8a466">${c.days_total - c.days_with_data} days without data${d.category ? '' : ': no fair comparison'}</span>` : '',
          ].filter(Boolean).join('<br/>');
        },
      },
      series: [{ type: 'custom', data: drawn, cursor: 'pointer', encode: { x: 0, y: 1 }, renderItem }],
    };
  }, [model]);

  const seg = (active: boolean) => `rounded-f2 px-f3 py-f1 text-cap transition-colors ${active ? 'bg-raised text-ink' : 'text-muted hover:text-ink'}`;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center justify-between gap-f3">
        {/* contents: Days sits left, the colour switch right */}
        <div className="contents">
        <button
          onClick={() => setCalView('days')}
          className="chip flex items-center gap-f1 rounded-f3 border border-line px-f3 py-f1 text-cap text-ink-2 transition-colors hover:text-ink"
          title={`Show ${MONTHS_LONG[month - 1]} ${year} day by day`}
        >
          <Icon icon={CalendarDays} size="xs" />
          Days
        </button>
        <div className="flex shrink-0 rounded-f3 border border-line p-f1" role="group" aria-label="Colour the calendar by">
          <button className={seg(mode === 'count')} onClick={() => setMode('count')} aria-pressed={mode === 'count'}>Amount</button>
          <button className={seg(mode === 'anomaly')} onClick={() => setMode('anomaly')} aria-pressed={mode === 'anomaly'}>vs usual</button>
        </div>
        </div>
      </div>

      <div className="relative mt-f3 min-h-[280px] flex-1">
        {box && model && (
          <EChart
            option={option}
            ariaLabel="Calendar of hotspot detections by year and month"
            onClick={(p) => {
              const d = (p.data as Drawn | undefined)?.cell;
              if (!d) return;
              // a second click (or tap) on the selected month opens its days: a double-click on
              // desktop, "tap, tap again" on a phone, where double-tap is not a reliable gesture
              if (d.year === year && d.month === month) setCalView('days');
              else setMonth(d.year, d.month);
            }}
          />
        )}
        {box && !model && !error && <div className="absolute inset-0 animate-pulse rounded-f3 bg-raised/40" />}
        {error && <p className="py-f5 text-body text-ember-soft">{friendlyError(error)}</p>}
      </div>

      <div className="mt-f3 flex items-center justify-between gap-f4 text-cap text-muted">
        {mode === 'count' ? (
          <div className="flex items-center gap-f2">
            <span>0</span>
            <span className="flex h-2 w-24 overflow-hidden rounded-full">
              {EMBER.map((c) => <span key={c} className="flex-1" style={{ background: c }} />)}
            </span>
            <span>{model ? `${compact(model.p95)}+` : ''}</span>
          </div>
        ) : (
          <div className="flex items-center gap-f2">
            <span>Lower</span>
            <span className="flex h-2 w-24 overflow-hidden rounded-full">
              {CATEGORIES.map((c) => <span key={c} className="flex-1" style={{ background: CATEGORY_COLOR[c] }} />)}
            </span>
            <span>Higher than usual</span>
          </div>
        )}
        <Popover
          up
          align="right"
          ariaLabel="How to read the calendar"
          buttonClassName="flex items-center gap-f2 rounded-full px-f2 text-cap text-muted transition-colors hover:text-ink"
          panelClassName="w-64"
          label={<><Icon icon={CircleHelp} size="xs" />How to read</>}
        >
          <ul className="space-y-f3 text-cap text-ink-2">
            <li className="flex items-center gap-f3"><Swatch kind="model" />Harmonized from MODIS by Harmonic (before 2012)</li>
            <li className="flex items-center gap-f3"><Swatch kind="partial" />Some days have no data</li>
            <li className="flex items-center gap-f3"><Swatch kind="none" />No data at all</li>
            <li className="flex items-center gap-f3"><Swatch kind="progress" />Month still in progress</li>
            <li className="pt-f2 text-muted">
              {mode === 'count'
                ? 'Brighter = more detections. Grey year labels are modelled years.'
                : 'Each month is compared with the same month in every year. The colour shows where it ranks; the middle colour is the usual range (the middle half of years).'}
            </li>
          </ul>
        </Popover>
      </div>
    </div>
  );
}

function Swatch({ kind }: { kind: 'model' | 'partial' | 'none' | 'progress' }) {
  return (
    <svg width="18" height="13" viewBox="0 0 18 13" aria-hidden className="shrink-0">
      <rect x="0.5" y="0.5" width="17" height="12" rx="2" fill={kind === 'none' ? NO_DATA : '#93391d'} opacity={kind === 'progress' ? 0.55 : 1} />
      {kind === 'model' && <rect x="2.5" y="2.5" width="13" height="8" rx="1.5" fill="none" stroke="rgba(240,239,236,0.5)" strokeDasharray="2 2" />}
      {(kind === 'partial' || kind === 'none') && <line x1="3" y1="10" x2="15" y2="3" stroke="rgba(240,239,236,0.6)" />}
      {kind === 'none' && <line x1="3" y1="3" x2="15" y2="10" stroke="rgba(240,239,236,0.6)" />}
    </svg>
  );
}
