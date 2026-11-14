import { useRef } from 'react';
import { USING_MOCK } from '../api/client';
import { REGION } from '../config/regions';
import AboutDialog from './AboutDialog';

export default function Header() {
  const about = useRef<HTMLDialogElement>(null);
  return (
    <header className="flex h-12 shrink-0 items-center justify-between gap-4 border-b border-line px-4">
      <div className="flex min-w-0 items-baseline gap-3">
        <span className="text-base font-semibold tracking-[0.2em] text-ink">AGNI</span>
        <span className="hidden truncate text-xs text-muted sm:inline">Active-fire Global Normalized Inventory</span>
      </div>
      <div className="flex items-center gap-2">
        {USING_MOCK && (
          <span className="rounded-full border border-line px-2.5 py-0.5 text-xs text-muted" title="The backend is not connected yet. Numbers are synthetic.">
            Demo data
          </span>
        )}
        <span className="rounded-full bg-raised px-3 py-0.5 text-sm text-ink-2">{REGION.name}</span>
        <button
          onClick={() => about.current?.showModal()}
          className="rounded-full px-2.5 py-0.5 text-sm text-muted transition-colors hover:bg-raised hover:text-ink"
        >
          About
        </button>
      </div>
      <AboutDialog ref={about} />
    </header>
  );
}
