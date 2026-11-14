import { useEffect, useId, useRef, useState, type ReactNode } from 'react';

interface Props {
  /** button contents */
  label: ReactNode;
  ariaLabel?: string;
  buttonClassName?: string;
  /** which edge of the button the panel lines up with */
  align?: 'left' | 'right';
  /** open upwards (for buttons near the bottom of the screen) */
  up?: boolean;
  panelClassName?: string;
  children: ReactNode | ((close: () => void) => ReactNode);
}

/** Small click-to-open panel. Closes on outside click and Escape. */
export default function Popover({ label, ariaLabel, buttonClassName = '', align = 'left', up = false, panelClassName = '', children }: Props) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const id = useId();
  const close = () => setOpen(false);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div ref={root} className="relative">
      <button type="button" aria-label={ariaLabel} aria-expanded={open} aria-controls={id} onClick={() => setOpen((o) => !o)} className={buttonClassName}>
        {label}
      </button>
      {open && (
        <div
          id={id}
          className={`absolute z-30 ${up ? 'bottom-full mb-2' : 'top-full mt-2'} ${align === 'right' ? 'right-0' : 'left-0'} rounded-xl border border-line bg-raised p-3 shadow-2xl shadow-black/50 ${panelClassName}`}
        >
          {typeof children === 'function' ? children(close) : children}
        </div>
      )}
    </div>
  );
}
