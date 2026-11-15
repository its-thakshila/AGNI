import { useRef } from 'react';
import { Info } from 'lucide-react';
import logoWithName from '../assets/brand/agni-logo-text.svg';
import { useMeta } from '../hooks/useData';
import AboutDialog from './AboutDialog';
import Icon from './ui/Icon';

export default function Header() {
  const about = useRef<HTMLDialogElement>(null);
  const demo = useMeta().data?.dataset === 'mock';
  return (
    <header className="flex h-f7 shrink-0 items-center justify-between gap-f4 px-f5">
      {/* the logo with the full name everywhere; a little smaller on phones */}
      <div className="flex min-w-0 items-center">
        <img src={logoWithName} alt="AGNI | Active-fire Global Normalized Inventory" className="h-[26px] w-auto sm:h-f6" />
      </div>
      <div className="flex items-center gap-f3">
        {demo && (
          <span className="chip whitespace-nowrap rounded-full border border-line px-f3 py-f1 text-cap text-muted" title="This is the mock dataset: the numbers are synthetic.">
            Demo data
          </span>
        )}
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
