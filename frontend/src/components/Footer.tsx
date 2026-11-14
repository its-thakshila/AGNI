export default function Footer() {
  return (
    <footer className="mx-auto max-w-6xl px-4 pb-10 pt-6 text-xs leading-relaxed text-muted sm:px-6">
      <p>
        Counts are satellite <em>detections</em> (375 m pixels flagged as burning), not individual fires. A large fire
        produces many detections. Years before 2012 are rebuilt from MODIS by a model trained on 2012–2025, so they are
        reliable as monthly totals (about ±3–5%), not as exact fire locations. No detections can also mean cloud,
        smoke, or a satellite outage.
      </p>
      <p className="mt-2">Source: NASA FIRMS (MODIS, VIIRS S-NPP), harmonized by the AGNI team · NASA Space Apps 2026</p>
    </footer>
  );
}
