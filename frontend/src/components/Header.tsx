import { USING_MOCK } from '../api/client';
import { REGION } from '../config/regions';

export default function Header() {
  return (
    <header className="flex items-center justify-between gap-4 border-b border-line px-4 py-3 sm:px-6">
      <div className="flex items-baseline gap-3 min-w-0">
        <span className="text-lg font-semibold tracking-[0.18em] text-ink">AGNI</span>
        <span className="hidden truncate text-sm text-muted sm:inline">Active-fire Global Normalized Inventory</span>
      </div>
      <div className="flex items-center gap-2">
        {USING_MOCK && (
          <span
            className="rounded-full border border-line px-2.5 py-1 text-xs text-muted"
            title="The backend is not connected yet. Numbers are synthetic."
          >
            Demo data
          </span>
        )}
        <span className="rounded-full bg-raised px-3 py-1 text-sm text-ink-2">{REGION.name}</span>
      </div>
    </header>
  );
}
