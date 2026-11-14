// Focus rings for keyboard users only. Tab (or Shift+Tab) switches keyboard mode on; any mouse,
// pen or touch press switches it off. index.css draws :focus-visible only in keyboard mode.
export function trackFocusMode(root: HTMLElement = document.documentElement): void {
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Tab') root.setAttribute('data-keyboard', '');
  }, true);
  window.addEventListener('pointerdown', () => root.removeAttribute('data-keyboard'), true);
}
