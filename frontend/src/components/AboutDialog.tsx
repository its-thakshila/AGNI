import type { Ref } from 'react';
import { X } from 'lucide-react';
import { useMeta } from '../hooks/useData';
import Icon from './ui/Icon';

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h3 className="mb-f2 text-body font-medium text-ink">{title}</h3>
      {children}
    </section>
  );
}

export default function AboutDialog({ ref }: { ref: Ref<HTMLDialogElement> }) {
  const { data: meta } = useMeta();
  return (
    <dialog
      ref={ref}
      onClick={(e) => { if (e.target === e.currentTarget) e.currentTarget.close(); }}
      className="m-auto w-[min(34rem,calc(100%-2rem))] rounded-f5 border border-line bg-surface p-0 text-ink backdrop:bg-black/60"
    >
      <div className="p-f6">
        <div className="flex items-start justify-between gap-f4">
          <div>
            <h2 className="text-title font-semibold">About AGNI</h2>
            <p className="text-body text-muted">Active-fire Global Normalized Inventory</p>
          </div>
          <form method="dialog">
            <button className="grid size-f6 place-items-center rounded-full text-muted transition-colors hover:bg-raised hover:text-ink" aria-label="Close">
              <Icon icon={X} />
            </button>
          </form>
        </div>

        <div className="mt-f5 space-y-f5 text-body text-ink-2">
          <p>
            AGNI shows how much burning satellites have seen in an area, month by month, from November 2000 to today. It joins
            two satellite records into one consistent series, so years can be compared fairly.
          </p>
          <Section title="What the numbers mean">
            <p>
              Counts are <em>detections</em>: 375 m pixels flagged as burning on a satellite pass. A large fire produces many
              detections, so they measure burning activity, not a number of fires. No detections can also mean cloud, smoke, or
              a satellite outage.
            </p>
          </Section>
          <Section title="Where the data comes from">
            <ul className="divide-y divide-line rounded-f4 border border-line">
              {(meta?.periods ?? []).map((p) => (
                <li key={p.from} className="flex justify-between gap-f4 px-f4 py-f3">
                  <span>{p.label}</span>
                  <span className="tabular shrink-0 text-muted">
                    {p.from.slice(0, 4)}–{p.to ? p.to.slice(0, 4) : 'now'}
                    {p.uncertainty ? ` · ±${Math.round(p.uncertainty * 100)}% daily` : ''}
                  </span>
                </li>
              ))}
            </ul>
            <p className="mt-f3">
              Before 2012, VIIRS did not exist yet. For those years, <span className="font-medium text-ink">Harmonic</span>,
              AGNI’s harmonization model, converts MODIS observations into VIIRS-equivalent detections. It was trained on
              2012–2025, when both satellites flew together, and also fills days when VIIRS was offline. Its months are
              reliable as totals (about ±3–5%), not as exact fire locations.
            </p>
          </Section>
          {!!meta?.known_gaps.length && (
            <Section title="Days with no data">
              <p>{meta.known_gaps.map((g) => `${g.from} to ${g.to} (${g.reason})`).join('; ')}.</p>
            </Section>
          )}
          <p className="text-cap text-muted">
            Source: NASA FIRMS (MODIS, VIIRS S-NPP), harmonized by Harmonic · AGNI team · NASA Space Apps Challenge 2026
            {meta?.dataset === 'mock' ? ' · This preview runs on synthetic demo data.' : ''}
          </p>
        </div>
      </div>
    </dialog>
  );
}
