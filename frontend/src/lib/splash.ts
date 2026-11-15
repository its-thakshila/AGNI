// The splash screen lives in index.html so it shows before the app's code loads.
// The app calls hideSplash() once the first view is ready; it fades out and is removed.

const shownAt = performance.now();
const MIN_MS = 700;      // avoid a flash when everything is cached
const MAX_MS = 15000;    // never keep the user waiting on a slow request
let done = false;

export function hideSplash() {
  if (done) return;
  done = true;
  const el = document.getElementById('splash');
  if (!el) return;
  const wait = Math.max(0, MIN_MS - (performance.now() - shownAt));
  window.setTimeout(() => {
    el.classList.add('splash-out');
    window.setTimeout(() => el.remove(), 500);
  }, wait);
}

window.setTimeout(hideSplash, MAX_MS);
