import { useMemo } from 'react';
import { FlaskConical } from 'lucide-react';
import { friendlyError } from '../../api/client';
import type { ForecastCell, Lead } from '../../api/types';
import { useBox, useForecast, useForecastCells } from '../../hooks/useData';
import { useSelection } from '../../store/useSelection';
import { REGION } from '../../config/regions';
import { compact, fmt } from '../../lib/stats';
import { FORECAST_COLORS } from '../../lib/colors';
import { FORECAST_LEVELS } from '../../lib/hotspotShapes';
import Icon from '../ui/Icon';

const LEADS: Lead[] = [1, 3, 7];
const LEAD_LABEL: Record<Lead, string> = { 1: 'Tomorrow', 3: 'In 3 days', 7: 'In 7 days' };

const dayText = (iso: string) =>
  new Date(`${iso}T00:00:00Z`).toLocaleDateString('en-AU', { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC' });

/** Cells whose centre lies in the box. */
function inBox(cells: ForecastCell[], b: { min_lat: number; max_lat: number; min_lon: number; max_lon: number }) {
  return cells.filter((c) => {
    const lat = c.lat + 0.05, lon = c.lon + 0.05;
    return lat >= b.min_lat && lat <= b.max_lat && lon >= b.min_lon && lon <= b.max_lon;
  });
}

/** Experimental fire forecast: 1, 3 and 7 days ahead, from recent fires and the weather forecast. */
export default function ForecastTab() {
  const { data: s, error, isPending } = useForecast();
  const cellsQ = useForecastCells();
  const { lead, setLead, aoi } = useSelection();
  const { box } = useBox();

  const area = useMemo(() => {
    const cells = cellsQ.data;
    if (!cells || !aoi || !box) return null;
    const inside = inBox(cells, box);
    return { expected: inside.reduce((a, c) => a + c.expected, 0), likely: inside.filter((c) => c.p >= 0.5).length };
  }, [cellsQ.data, aoi, box]);
  const likelyAll = useMemo(() => cellsQ.data?.filter((c) => c.p >= 0.5).length ?? null, [cellsQ.data]);

  if (error) return <p className="py-f5 text-body text-ember-soft">{friendlyError(error)}</p>;
  if (isPending) return <div className="flex-1 animate-pulse rounded-f3 bg-raised/40" />;
  if (!s) {
    return (
      <div className="py-f5 text-body text-muted">
        <ExperimentalNote />
        <p className="mt-f4">No forecast for this dataset yet. It is made once a day from the latest fire data.</p>
      </div>
    );
  }

  const L = s.leads[String(lead)];
  const acc = s.accuracy[String(lead)];
  const expected = area ? area.expected : L?.expected ?? 0;
  const likely = area ? area.likely : likelyAll;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-f4">
      <ExperimentalNote error={acc?.daily_total_error_pct} />

      {/* the three forecast days; picking one also sets the map */}
      <div role="radiogroup" aria-label="Forecast day" className="grid grid-cols-3 gap-f3">
        {LEADS.filter((l) => s.leads[String(l)]).map((l) => {
          const x = s.leads[String(l)];
          const on = l === lead;
          return (
            <button
              key={l}
              role="radio"
              aria-checked={on}
              onClick={() => setLead(l)}
              className={`min-w-0 rounded-f4 border px-f3 py-f3 text-left transition-colors ${
                on ? 'border-[#a37bf0]/70 bg-[#a37bf0]/10' : 'border-line/70 bg-page/35 hover:border-ink-2/30'
              }`}
            >
              <div className="truncate text-cap text-muted">{LEAD_LABEL[l]}</div>
              <div className="truncate text-body font-medium text-ink">
                {new Date(`${x.target_date}T00:00:00Z`).toLocaleDateString('en-AU', { day: 'numeric', month: 'short', timeZone: 'UTC' })}
              </div>
              <div className="tabular text-cap text-ink-2">≈ {compact(x.expected)}</div>
            </button>
          );
        })}
      </div>

      <div className="rounded-f4 border border-line/70 bg-page/35 p-f4">
        <div className="flex items-end justify-between gap-f4">
          <div className="min-w-0">
            <div className="tabular text-display font-semibold text-ink">{fmt(expected)}</div>
            <div className="text-cap text-muted">
              expected detections · {L ? dayText(L.target_date) : ''} · {area ? 'custom area' : `all of ${REGION.name}`}
            </div>
          </div>
          {/* the model's tested error, not its own spread (that treats cells as independent and is far too narrow) */}
          {acc && expected > 0 && (
            <div className="shrink-0 text-right">
              <div className="tabular text-body text-ink-2">
                {fmt(expected * (1 - acc.daily_total_error_pct / 100))}–{fmt(expected * (1 + acc.daily_total_error_pct / 100))}
              </div>
              <div className="text-cap text-muted">typical range (±{Math.round(acc.daily_total_error_pct)}%)</div>
            </div>
          )}
        </div>
        {likely !== null && (
          <p className="mt-f3 text-cap text-muted">
            {likely === 0 ? 'No place has a 50% or higher chance of a fire detection.'
              : `${fmt(likely)} ${likely === 1 ? 'place' : 'places'} (0.1° cells) with a 50% or higher chance of a fire detection.`}
          </p>
        )}
      </div>

      {acc && (
        <div className="rounded-f4 border border-line/70 bg-page/35 p-f4 text-cap text-muted">
          <div className="mb-f2 text-body font-medium text-ink">How good is it?</div>
          <p>
            Tested on {s.test_years?.join(', ') ?? 'held-out years'}: daily totals were off by about{' '}
            <span className="text-ink-2">{Math.round(acc.daily_total_error_pct)}%</span> on average, against{' '}
            {Math.round(acc.persistence_error_pct)}% for simply repeating the last day’s fires. The {fmt(1000)} highest-chance places held{' '}
            <span className="text-ink-2">{Math.round(acc.top1000_capture_pct)}%</span> of the detections.
          </p>
          <p className="mt-f2">Use it as a guide to where fire is likely, not as a prediction of exact places. Accuracy falls with each day ahead.</p>
        </div>
      )}

      <p className="mt-auto text-cap text-muted">
        Made from fires up to {dayText(s.issue_date)} · observed weather: NASA POWER · weather forecast:{' '}
        <a href="https://open-meteo.com/" target="_blank" rel="noopener" className="text-ink-2 underline decoration-line underline-offset-2 hover:text-ink">
          Open-Meteo.com
        </a>{' '}
        (CC BY 4.0) · updated daily
      </p>
    </div>
  );
}

function ExperimentalNote({ error }: { error?: number }) {
  return (
    <div className="flex items-start gap-f3 rounded-f4 border border-[#a37bf0]/40 bg-[#a37bf0]/[0.07] px-f4 py-f3">
      <Icon icon={FlaskConical} size="sm" className="mt-[2px] shrink-0 text-[#c4a6ff]" />
      <p className="text-cap text-ink-2">
        <span className="font-medium text-[#c4a6ff]">Experimental.</span> A machine-learning forecast from recent fires and the
        weather forecast{error ? `; daily totals are typically off by about ${Math.round(error)}%` : ''}.
      </p>
    </div>
  );
}

/** Legend for the forecast map: chance of at least one fire detection. */
export function ForecastLegend() {
  return (
    <div className="flex items-center gap-f3 text-cap text-muted">
      <span>Chance of fire</span>
      <span className="flex gap-f1">
        {FORECAST_COLORS.map((c) => <span key={c} className="h-2 w-5 rounded-f2" style={{ background: c }} />)}
      </span>
      <span>{Math.round(FORECAST_LEVELS[0] * 100)}–{Math.round(FORECAST_LEVELS[FORECAST_LEVELS.length - 1] * 100)}%+</span>
    </div>
  );
}
