import type { Warning } from '../api/types';

// Warnings the UI already explains in its own way (legend, labels), so they're not repeated as text.
const SHOWN_ELSEWHERE = new Set(['MONTH_IN_PROGRESS']);

export default function Notices({ warnings }: { warnings: Warning[] | undefined }) {
  const seen = new Set<string>();
  const list = (warnings ?? []).filter((w) => !SHOWN_ELSEWHERE.has(w.code) && !seen.has(w.code) && seen.add(w.code));
  if (!list.length) return null;
  return (
    <ul className="mt-3 space-y-1 text-xs text-muted" aria-label="Notes about this data">
      {list.map((w) => (
        <li key={w.code} className="flex gap-2">
          <span aria-hidden className="mt-[5px] inline-block size-1.5 shrink-0 rounded-full bg-ember-soft/70" />
          <span>{w.message}</span>
        </li>
      ))}
    </ul>
  );
}
