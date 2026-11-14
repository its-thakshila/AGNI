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
 * 1px lower. Body labels (sm) are capitalised words ("About", "Calendar", "Draw area"): flex
 * centring puts the icon on the x-height centre, 0.9px below the capitals' centre, so the icon
 * hangs low; it is nudged up 0.06em onto the cap-height centre (measured in the browser).
 */
export default function Icon({ icon: I, size = 'md', className = '', ...rest }: { icon: LucideIcon; size?: Size } & Omit<LucideProps, 'size' | 'ref'>) {
  const nudge = size === 'xs' ? 'relative top-[0.07em]' : size === 'sm' ? 'relative -top-[0.06em]' : '';
  return <I size={PX[size]} strokeWidth={size === 'xs' ? 2 : 1.75} aria-hidden className={`${nudge} ${className}`.trim()} {...rest} />;
}
