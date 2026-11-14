/**
 * AGNI Worker (Cloudflare Workers free plan).
 *
 *   GET|HEAD /data/<dataset>/<file>   serve a dataset file from R2 (supports Range, for DuckDB-WASM)
 *   GET      /pending/<dataset>       days currently being processed
 *   POST     /ensure                  {dataset, start, end}: start processing days that aren't stored yet
 *
 * The Worker never talks to FIRMS and never runs the model: it starts the GitHub Actions
 * pipeline (which holds the FIRMS key and runs Harmonic) and serves what the pipeline stored.
 */

export interface Env {
  DATA: R2Bucket;
  DATASETS: string; // comma list, e.g. "mock,real"
  ALLOWED_ORIGINS: string; // comma list of site origins allowed to call the Worker
  GITHUB_REPO: string; // owner/name
  GITHUB_WORKFLOW: string; // pipeline.yml
  GITHUB_REF: string; // branch to run the workflow on
  DISPATCH_TOKEN?: string; // secret: fine-grained GitHub token with Actions: read & write on the repo
}

const FILE = /^(manifest\.json|hist\/[a-z_]+\.parquet|hist\/rows\/\d{4}\.parquet|live\/(cells|rows)\/\d{4}-\d{2}\.parquet)$/;
const DAY = /^\d{4}-\d{2}-\d{2}$/;
const MAX_DAYS = 62; // per request
const PENDING_TTL_MS = 15 * 60_000; // a day stays "being processed" for at most 15 minutes
const RETRY_NONE_MS = 6 * 3600_000; // "no data" days are retried after 6 hours
const DISPATCH_GAP_MS = 30_000; // at most one workflow start per 30 seconds

interface ManifestDay { has_data: string; processed_at?: string }
interface Manifest { local_end: string; days: Record<string, ManifestDay> }

function cors(request: Request, env: Env): Record<string, string> {
  const origin = request.headers.get('Origin') ?? '';
  const allowed = env.ALLOWED_ORIGINS.split(',').map((o) => o.trim());
  return {
    ...(allowed.includes(origin) ? { 'Access-Control-Allow-Origin': origin, Vary: 'Origin' } : {}),
    'Access-Control-Allow-Methods': 'GET, HEAD, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Range, Content-Type',
    'Access-Control-Expose-Headers': 'Content-Range, Content-Length, Accept-Ranges, ETag',
    'Access-Control-Max-Age': '86400',
  };
}

const json = (body: unknown, status: number, headers: Record<string, string>) =>
  new Response(JSON.stringify(body), { status, headers: { ...headers, 'Content-Type': 'application/json', 'Cache-Control': 'no-store' } });

function parseRange(header: string | null): R2Range | undefined {
  const m = header?.match(/^bytes=(\d*)-(\d*)$/);
  if (!m || (!m[1] && !m[2])) return undefined;
  if (!m[1]) return { suffix: Number(m[2]) };
  const offset = Number(m[1]);
  return m[2] ? { offset, length: Number(m[2]) - offset + 1 } : { offset };
}

async function serveFile(request: Request, env: Env, dataset: string, file: string, headers: Record<string, string>) {
  const key = `${dataset}/${file}`;
  // The site asks for data files with ?v=<version>, a new URL whenever the file changes, so those
  // can be cached by the browser for good: repeat visits read them without any network round trip.
  const versioned = new URL(request.url).searchParams.has('v');
  const cache = file === 'manifest.json' ? 'no-store'
    : versioned ? 'public, max-age=31536000, immutable'
    : file.startsWith('hist/') ? 'public, max-age=3600' : 'public, max-age=60';
  const base = { ...headers, 'Accept-Ranges': 'bytes', 'Cache-Control': cache, 'Content-Type': file.endsWith('.json') ? 'application/json' : 'application/octet-stream' };

  if (request.method === 'HEAD') {
    const head = await env.DATA.head(key);
    if (!head) return new Response(null, { status: 404, headers });
    // DuckDB-WASM probes with `HEAD` + `Range: bytes=0-` and reads a file in parts only if the
    // answer is 206 with the full size; a plain 200 makes it download the whole file instead.
    if (request.headers.has('Range')) {
      return new Response(null, {
        status: 206,
        headers: { ...base, 'Content-Length': String(head.size), 'Content-Range': `bytes 0-${head.size - 1}/${head.size}`, ETag: head.httpEtag },
      });
    }
    return new Response(null, { status: 200, headers: { ...base, 'Content-Length': String(head.size), ETag: head.httpEtag } });
  }
  const range = parseRange(request.headers.get('Range'));
  const object = await env.DATA.get(key, range ? { range } : undefined);
  if (!object) return new Response('Not found', { status: 404, headers });
  if (!range) {
    return new Response(object.body, { status: 200, headers: { ...base, 'Content-Length': String(object.size), ETag: object.httpEtag } });
  }
  const r = object.range as { offset?: number; length?: number; suffix?: number };
  const offset = r.suffix !== undefined ? object.size - r.suffix : r.offset ?? 0;
  const length = r.suffix !== undefined ? r.suffix : r.length ?? object.size - offset;
  return new Response(object.body, {
    status: 206,
    headers: { ...base, 'Content-Length': String(length), 'Content-Range': `bytes ${offset}-${offset + length - 1}/${object.size}`, ETag: object.httpEtag },
  });
}

async function pendingDays(env: Env, dataset: string): Promise<string[]> {
  const list = await env.DATA.list({ prefix: `${dataset}/pending/`, include: ['customMetadata'] });
  const now = Date.now();
  return list.objects
    .filter((o) => !o.key.endsWith('/_dispatch') && now - o.uploaded.getTime() < PENDING_TTL_MS)
    .map((o) => o.key.slice(`${dataset}/pending/`.length))
    .sort();
}

function* daysBetween(start: string, end: string) {
  for (let t = Date.parse(`${start}T00:00:00Z`), e = Date.parse(`${end}T00:00:00Z`); t <= e; t += 86_400_000) {
    yield new Date(t).toISOString().slice(0, 10);
  }
}

/** Collapse sorted days into "a:b,c" ranges for the workflow input. */
function ranges(days: string[]): string {
  const out: string[] = [];
  let first = days[0], prev = days[0];
  for (const d of [...days.slice(1), '']) {
    const next = new Date(Date.parse(`${prev}T00:00:00Z`) + 86_400_000).toISOString().slice(0, 10);
    if (d === next) { prev = d; continue; }
    out.push(first === prev ? first : `${first}:${prev}`);
    first = prev = d;
  }
  return out.join(',');
}

async function ensure(request: Request, env: Env, headers: Record<string, string>) {
  const body = (await request.json().catch(() => null)) as { dataset?: string; start?: string; end?: string } | null;
  const dataset = body?.dataset ?? '';
  if (!env.DATASETS.split(',').includes(dataset) || !DAY.test(body?.start ?? '') || !DAY.test(body?.end ?? '')) {
    return json({ error: 'Send {dataset, start, end} with dates as YYYY-MM-DD.' }, 400, headers);
  }
  const manifestObject = await env.DATA.get(`${dataset}/manifest.json`);
  if (!manifestObject) return json({ error: 'Dataset not found.' }, 404, headers);
  const manifest = (await manifestObject.json()) as Manifest;

  const today = new Date().toISOString().slice(0, 10);
  const afterCsv = new Date(Date.parse(`${manifest.local_end}T00:00:00Z`) + 86_400_000).toISOString().slice(0, 10);
  const first = body!.start! > afterCsv ? body!.start! : afterCsv;
  const last = body!.end! < today ? body!.end! : today;
  const wanted = first <= last ? [...daysBetween(first, last)].slice(0, MAX_DAYS) : [];

  const now = Date.now();
  const ready = wanted.filter((d) => {
    const entry = manifest.days[d];
    if (!entry) return false;
    return entry.has_data !== 'none' || now - Date.parse(entry.processed_at ?? '1970-01-01') < RETRY_NONE_MS;
  });
  const pending = await pendingDays(env, dataset);
  const toStart = wanted.filter((d) => !ready.includes(d) && !pending.includes(d));

  let started: string[] = [];
  if (toStart.length) {
    if (!env.DISPATCH_TOKEN) return json({ ready, pending, started, error: 'Processing is not configured (DISPATCH_TOKEN).' }, 503, headers);
    const last = await env.DATA.head(`${dataset}/pending/_dispatch`);
    if (!last || now - last.uploaded.getTime() > DISPATCH_GAP_MS) {
      const res = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/${env.GITHUB_WORKFLOW}/dispatches`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${env.DISPATCH_TOKEN}`,
          Accept: 'application/vnd.github+json',
          'User-Agent': 'agni-worker',
          'X-GitHub-Api-Version': '2022-11-28',
        },
        body: JSON.stringify({ ref: env.GITHUB_REF, inputs: { dataset, days: ranges(toStart) } }),
      });
      if (res.status !== 204) {
        return json({ ready, pending, started, error: `Could not start processing (GitHub ${res.status}).` }, 502, headers);
      }
      await env.DATA.put(`${dataset}/pending/_dispatch`, new Date().toISOString());
      await Promise.all(toStart.map((d) => env.DATA.put(`${dataset}/pending/${d}`, new Date().toISOString())));
      started = toStart;
    }
  }
  return json({ ready, pending: [...new Set([...pending, ...started])].sort(), started }, 200, headers);
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const headers = cors(request, env);
    if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers });
    const url = new URL(request.url);
    const parts = url.pathname.split('/').filter(Boolean);

    if (parts[0] === 'data' && (request.method === 'GET' || request.method === 'HEAD')) {
      const dataset = parts[1] ?? '';
      const file = parts.slice(2).join('/');
      if (!env.DATASETS.split(',').includes(dataset) || !FILE.test(file)) return new Response('Not found', { status: 404, headers });
      return serveFile(request, env, dataset, file, headers);
    }
    if (parts[0] === 'pending' && request.method === 'GET' && env.DATASETS.split(',').includes(parts[1] ?? '')) {
      return json({ pending: await pendingDays(env, parts[1]) }, 200, headers);
    }
    if (parts[0] === 'ensure' && request.method === 'POST') return ensure(request, env, headers);
    if (parts.length === 0) return json({ service: 'AGNI data', ok: true }, 200, headers);
    return new Response('Not found', { status: 404, headers });
  },
};
