import type { ReactNode, Ref } from 'react';
import { ExternalLink, X } from 'lucide-react';
import logo from '../assets/brand/agni-logo.svg';
import { useMeta } from '../hooks/useData';
import { TEAM } from '../config/team';
import { REGION } from '../config/regions';
import Icon from './ui/Icon';

const VERSION = '1.0';

const day = (iso: string) =>
  new Date(iso).toLocaleDateString('en-AU', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[6.5rem_1fr] gap-f3 py-f2">
      <dt className="text-muted">{label}</dt>
      <dd className="text-ink-2">{children}</dd>
    </div>
  );
}

/** A standard About box: what AGNI is, the essentials, who made it. */
export default function AboutDialog({ ref }: { ref: Ref<HTMLDialogElement> }) {
  const { data: meta } = useMeta();
  const start = meta ? day(meta.coverage.record_start) : 'Nov 2000';
  return (
    <dialog
      ref={ref}
      onClick={(e) => { if (e.target === e.currentTarget) e.currentTarget.close(); }}
      className="scroll-thin m-auto max-h-[calc(100dvh-2rem)] w-[min(27rem,calc(100%-2rem))] overflow-y-auto rounded-f5 border border-line bg-surface p-0 text-ink backdrop:bg-black/70"
    >
      <div className="relative px-f6 pb-f5 pt-f6 text-center">
        <form method="dialog" className="absolute right-f4 top-f4">
          <button className="grid size-f6 place-items-center rounded-full text-muted transition-colors hover:bg-raised hover:text-ink" aria-label="Close">
            <Icon icon={X} />
          </button>
        </form>
        <h2>
          <img src={logo} alt="AGNI" className="mx-auto h-[44px] w-auto" />
        </h2>
        <p className="mt-f3 text-body text-muted">Active-fire Global Normalized Inventory</p>
        <p className="mt-f1 text-cap text-muted">Version {VERSION}</p>
        <p className="mt-f4 text-body text-ink-2">
          A consistent record of active-fire detections over {REGION.name}, joining NASA’s MODIS and VIIRS satellites into one
          series so every year can be compared fairly.
        </p>
      </div>

      <dl className="divide-y divide-line border-y border-line px-f6 text-cap">
        <Row label="Coverage">{REGION.name} · {start} to today</Row>
        <Row label="Data">NASA FIRMS: VIIRS (S-NPP, NOAA-20/21) and MODIS (Terra, Aqua)</Row>
        <Row label="Model">Harmonic converts MODIS to VIIRS-equivalent detections (±3% monthly)</Row>
        {meta?.firms.last_fetch && <Row label="Updated">{day(meta.firms.last_fetch)}</Row>}
      </dl>

      <div className="px-f6 py-f5 text-center text-cap text-muted">
        <p className="text-body font-medium text-ink-2">{TEAM.name}</p>
        <p>{TEAM.event}</p>
        {TEAM.members.length > 0 && (
          <ul className="mt-f3 flex flex-wrap justify-center gap-x-f2 gap-y-f1">
            {TEAM.members.map((m) => (
              <li key={m.github}>
                <a
                  href={m.github}
                  target="_blank"
                  rel="noopener"
                  title={`${m.name} on GitHub`}
                  className="inline-block rounded-full px-f2 py-[2px] text-ink-2 transition-colors hover:bg-raised hover:text-ink"
                >
                  {m.name}
                </a>
              </li>
            ))}
          </ul>
        )}
        <a
          href={TEAM.repository}
          target="_blank"
          rel="noopener"
          className="mt-f3 inline-flex items-center gap-f1 rounded-full px-f3 py-f1 text-ink-2 transition-colors hover:bg-raised hover:text-ink"
        >
          Source code
          <Icon icon={ExternalLink} size="xs" />
        </a>
        <p className="mt-f3">
          Fire data: NASA FIRMS · Imagery: NASA GIBS · Map: © OpenStreetMap, CARTO
          {meta?.dataset === 'mock' ? ' · Demo data' : ''}
        </p>
      </div>
    </dialog>
  );
}
