import type { Ref } from 'react';
import { useMeta } from '../hooks/useData';
import { USING_MOCK } from '../api/client';

export default function AboutDialog({ ref }: { ref: Ref<HTMLDialogElement> }) {
  const { data: meta } = useMeta();
  return (
    <dialog
      ref={ref}
      onClick={(e) => { if (e.target === e.currentTarget) e.currentTarget.close(); }}
      className="m-auto w-[min(36rem,calc(100%-2rem))] rounded-2xl border border-line bg-surface p-0 text-ink backdrop:bg-black/60"
    >
      <div className="p-6">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold tracking-wide">About AGNI</h2>
            <p className="text-sm text-muted">Active-fire Global Normalized Inventory</p>
          </div>
          <form method="dialog">
            <button className="rounded-lg px-2 py-1 text-sm text-muted hover:bg-raised hover:text-ink" aria-label="Close">Close</button>
          </form>
        </div>

        <div className="mt-5 space-y-4 text-sm leading-relaxed text-ink-2">
          <p>
            AGNI shows how much burning satellites have seen in an area, month by month, from November 2000 to today. It joins
            two satellite records into one consistent series, so years can be compared fairly.
          </p>
          <div>
            <h3 className="mb-1 font-medium text-ink">What the numbers mean</h3>
            <p>
              Counts are <em>detections</em>: 375 m pixels flagged as burning on a satellite pass. A large fire produces many
              detections, so they are a measure of burning activity, not a count of fires. No detections can also mean cloud,
              smoke, or a satellite outage.
            </p>
          </div>
          <div>
            <h3 className="mb-1 font-medium text-ink">Where the data comes from</h3>
            <ul className="space-y-1">
              {(meta?.periods ?? []).map((p) => (
                <li key={p.from} className="flex justify-between gap-4">
                  <span>{p.label}</span>
                  <span className="tabular shrink-0 text-muted">
                    {p.from.slice(0, 4)}–{p.to ? p.to.slice(0, 4) : 'now'}
                    {p.uncertainty ? ` · ±${Math.round(p.uncertainty * 100)}% daily` : ''}
                  </span>
                </li>
              ))}
            </ul>
            <p className="mt-2">
              Before 2012, VIIRS did not exist yet. Those years are rebuilt from older MODIS observations by a model trained on
              2012–2025, when both satellites flew together. They are reliable as monthly totals (about ±3–5%), not as exact
              fire locations.
            </p>
          </div>
          {!!meta?.known_gaps.length && (
            <div>
              <h3 className="mb-1 font-medium text-ink">Days with no data</h3>
              <p>{meta.known_gaps.map((g) => `${g.from} to ${g.to} (${g.reason})`).join('; ')}.</p>
            </div>
          )}
          <p className="text-xs text-muted">
            Source: NASA FIRMS (MODIS, VIIRS S-NPP), harmonized by the AGNI team · NASA Space Apps Challenge 2026
            {USING_MOCK ? ' · This preview runs on synthetic demo data.' : ''}
          </p>
        </div>
      </div>
    </dialog>
  );
}
