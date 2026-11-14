import { useRef } from 'react';
import { Flame, Info, MapPin } from 'lucide-react';
import { USING_MOCK } from '../api/client';
import { REGION } from '../config/regions';
import AboutDialog from './AboutDialog';
import Icon from './ui/Icon';

export default function Header() {
  const about = useRef<HTMLDialogElement>(null);
  return (
    <header className="flex h-f7 shrink-0 items-center justify-between gap-f4 px-f5">
      <div className="flex min-w-0 items-center gap-f3">
        <span className="grid size-f6 shrink-0 place-items-center rounded-f3 bg-ember/12 text-ember">
          <Icon icon={Flame} />
        </span>
        <span className="text-lead font-semibold tracking-[0.18em] text-ink">AGNI</span>
        <span className="hidden truncate text-cap text-muted sm:inline">Active-fire Global Normalized Inventory</span>
      </div>
      <div className="flex items-center gap-f3">
        {USING_MOCK && (
          <span className="chip whitespace-nowrap rounded-full border border-line px-f3 py-f1 text-cap text-muted" title="The backend is not connected yet. Numbers are synthetic.">
            Demo data
          </span>
        )}
        <span className="chip flex items-center gap-f2 whitespace-nowrap rounded-full bg-raised px-f4 py-f1 text-body text-ink-2">
          <Icon icon={MapPin} size="sm" className="text-muted" />
          {REGION.name}
        </span>
        <button
          onClick={() => about.current?.showModal()}
          aria-label="About AGNI"
          className="flex items-center gap-f2 rounded-full px-f3 py-f1 text-body text-muted transition-colors hover:bg-raised hover:text-ink sm:px-f4"
        >
          <Icon icon={Info} size="sm" />
          <span className="hidden sm:inline">About</span>
        </button>
      </div>
      <AboutDialog ref={about} />
    </header>
  );
}
