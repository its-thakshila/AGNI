import { useSelection } from '../store/useSelection';
import Popover from './ui/Popover';

export default function SettingsMenu() {
  const { types, setTypes } = useSelection();
  const includeIndustry = types.includes(2);
  return (
    <Popover
      align="right"
      ariaLabel="Settings"
      buttonClassName="grid size-8 place-items-center rounded-full text-muted transition-colors hover:bg-raised hover:text-ink"
      panelClassName="w-72"
      label={
        <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden>
          <path d="M2 4.5h7M12 4.5h2M2 11.5h2M7 11.5h7" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
          <circle cx="10.5" cy="4.5" r="1.6" fill="none" stroke="currentColor" strokeWidth="1.4" />
          <circle cx="5.5" cy="11.5" r="1.6" fill="none" stroke="currentColor" strokeWidth="1.4" />
        </svg>
      }
    >
      <div className="text-xs text-muted">What to count</div>
      <label className="mt-2 flex cursor-pointer items-start gap-2 text-sm text-ink-2">
        <input type="checkbox" className="mt-1 accent-[#f2793a]" checked={includeIndustry} onChange={(e) => setTypes(e.target.checked ? [0, 2, 3] : [0])} />
        <span>
          Include industrial heat sources
          <span className="block text-xs text-muted">Gas flares, factories and offshore platforms. These burn all year and are not wildfires.</span>
        </span>
      </label>
    </Popover>
  );
}
