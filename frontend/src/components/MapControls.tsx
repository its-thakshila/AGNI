import { Check, Layers, Minus, Plus } from 'lucide-react';
import type { Map as MLMap } from 'maplibre-gl';
import { BASEMAPS, type Basemap } from '../lib/basemaps';
import Popover from './ui/Popover';
import Icon from './ui/Icon';

const niceDate = (d: string) =>
  new Date(`${d}T00:00:00Z`).toLocaleDateString('en-AU', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });

const glass = 'border border-line bg-page/85 backdrop-blur';
const btn = 'grid size-f6 place-items-center text-ink-2 transition-colors hover:bg-raised hover:text-ink';

/** Map background switcher and zoom buttons, in the app's own style (replacing MapLibre's). */
export default function MapControls({ map, basemap, onBasemap, dailyDate, dailyMissing }: {
  map: MLMap | null; basemap: Basemap; onBasemap: (b: Basemap) => void; dailyDate: string; dailyMissing: boolean;
}) {
  return (
    <div className="flex flex-col items-end gap-f2">
      <Popover
        left
        ariaLabel="Map background"
        buttonClassName={`${btn} rounded-full ${glass} ${basemap !== 'dark' ? 'text-ember' : ''}`}
        panelClassName="w-56 p-f2 sm:w-72"
        label={<Icon icon={Layers} size="sm" />}
      >
        {(close) => (
          <ul className="space-y-f1" role="listbox" aria-label="Map background">
            {BASEMAPS.map((b) => (
              <li key={b.id}>
                <button
                  role="option"
                  aria-selected={basemap === b.id}
                  onClick={() => { onBasemap(b.id); close(); }}
                  className={`flex w-full items-start gap-f3 rounded-f3 px-f3 py-f2 text-left transition-colors hover:bg-page/60 ${basemap === b.id ? 'bg-page/60' : ''}`}
                >
                  {/* one label line tall (1lh at the label's size), so the check centres on the label */}
                  <span className="flex h-[1lh] w-[14px] shrink-0 items-center justify-center text-body text-ember">
                    {basemap === b.id && <Icon icon={Check} size="sm" />}
                  </span>
                  <span className="min-w-0">
                    <span className="block text-body text-ink">{b.label}</span>
                    {/* phones: names only, so the menu fits beside the button on the small map */}
                    <span className="hidden text-cap text-muted sm:block">
                      {b.detail}
                      {b.id === 'daily' && dailyDate && <span className="whitespace-nowrap"> ({niceDate(dailyDate)})</span>}
                    </span>
                    {b.id === 'daily' && basemap === 'daily' && dailyMissing && (
                      <span className="mt-f1 block text-cap text-ember-soft">No NASA imagery for this day yet: showing cloud-free</span>
                    )}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Popover>
      {/* 34px wide including its border, like the round buttons, so the column's centres line up */}
      <div className={`box-border flex w-f6 flex-col overflow-hidden rounded-f4 ${glass}`} role="group" aria-label="Zoom">
        <button className={`${btn} w-full`} onClick={() => map?.zoomIn()} aria-label="Zoom in"><Icon icon={Plus} size="sm" /></button>
        <span className="mx-f2 h-px bg-line" aria-hidden />
        <button className={`${btn} w-full`} onClick={() => map?.zoomOut()} aria-label="Zoom out"><Icon icon={Minus} size="sm" /></button>
      </div>
    </div>
  );
}
