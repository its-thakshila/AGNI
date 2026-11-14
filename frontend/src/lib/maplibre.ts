// MapLibre v6 runs its tile parsing in an ES-module web worker. Vite's dependency
// pre-bundling breaks the default relative worker URL, so bundle the worker
// ourselves and hand MapLibre the URL.
import * as maplibregl from 'maplibre-gl';
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';

maplibregl.setWorkerUrl(workerUrl);

export default maplibregl;
