import type { LucideIcon, LucideProps } from 'lucide-react';

type Size = 'xs' | 'sm' | 'md';
const PX: Record<Size, number> = { xs: 12, sm: 14, md: 18 };

/**
 * Lucide icon at the design-system sizes, one stroke weight everywhere.
 *   xs 12px - beside 12px caption text
 *   sm 14px - beside 15px body text
 *   md 18px - on its own (icon-only buttons)
 * Caption icons (xs) are nudged down 0.07em: plain flex centring puts them on the cap-height
 * centre, but short lowercase captions ("Month details") read centred on the x-height, about
 * 1px lower. Measured: 15px labels already land between the two, so sm/md stay as they are.
 */
export default function Icon({ icon: I, size = 'md', className = '', ...rest }: { icon: LucideIcon; size?: Size } & Omit<LucideProps, 'size' | 'ref'>) {
  const nudge = size === 'xs' ? 'relative top-[0.07em]' : '';
  return <I size={PX[size]} strokeWidth={size === 'xs' ? 2 : 1.75} aria-hidden className={`${nudge} ${className}`.trim()} {...rest} />;
}
