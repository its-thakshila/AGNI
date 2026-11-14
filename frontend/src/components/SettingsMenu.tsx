import { SlidersHorizontal } from 'lucide-react';
import { useSelection } from '../store/useSelection';
import Popover from './ui/Popover';
import Icon from './ui/Icon';

export default function SettingsMenu() {
  const { types, setTypes } = useSelection();
  const includeIndustry = types.includes(2);
  return (
    <Popover
      align="right"
      ariaLabel="Settings"
      buttonClassName="grid size-f6 place-items-center rounded-f3 text-muted transition-colors hover:bg-raised hover:text-ink"
      panelClassName="w-72"
      label={<Icon icon={SlidersHorizontal} />}
    >
      <div className="text-cap text-muted">What to count</div>
      <label className="mt-f3 flex cursor-pointer items-start gap-f3 text-body text-ink-2">
        <input type="checkbox" className="mt-f2 accent-[#f2793a]" checked={includeIndustry} onChange={(e) => setTypes(e.target.checked ? [0, 2, 3] : [0])} />
        <span>
          Include industrial heat sources
          <span className="mt-f1 block text-cap text-muted">Gas flares, factories and offshore platforms. These burn all year and are not wildfires.</span>
        </span>
      </label>
    </Popover>
  );
}
