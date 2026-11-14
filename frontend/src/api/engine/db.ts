// DuckDB-WASM: the same SQL engine the backend uses, running in the browser. It reads the
// Parquet files over HTTP with Range requests, so only the parts a query needs are downloaded.
// The WebAssembly binary loads from jsDelivr on first use (about 7 MB compressed, then cached).

import * as duckdb from '@duckdb/duckdb-wasm';

let ready: Promise<duckdb.AsyncDuckDBConnection> | null = null;
let instance: duckdb.AsyncDuckDB | null = null;

export function connection(): Promise<duckdb.AsyncDuckDBConnection> {
  ready ??= (async () => {
    const bundle = await duckdb.selectBundle(duckdb.getJsDelivrBundles());
    const workerUrl = URL.createObjectURL(new Blob([`importScripts("${bundle.mainWorker!}");`], { type: 'text/javascript' }));
    const db = new duckdb.AsyncDuckDB(new duckdb.VoidLogger(), new Worker(workerUrl));
    await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
    URL.revokeObjectURL(workerUrl);
    instance = db;
    const conn = await db.connect();
    await conn.query(`SET TimeZone = 'UTC'`);
    // keep Parquet footers between queries, so repeat queries skip those round trips
    try { await conn.query('SET enable_object_cache = true'); } catch { /* setting renamed in newer DuckDB */ }
    return conn;
  })();
  return ready;
}

/** Run SQL and return plain objects. Cast BIGINT/DATE in SQL (to DOUBLE / VARCHAR) for clean JSON. */
export async function query<T = Record<string, unknown>>(sql: string): Promise<T[]> {
  const conn = await connection();
  const table = await conn.query(sql);
  return table.toArray().map((row) => row.toJSON() as T);
}

/** Run SQL that writes a file inside DuckDB's in-memory file system and return its bytes. */
export async function exportFile(sql: string, name: string): Promise<Uint8Array> {
  const conn = await connection();
  await conn.query(sql);
  const bytes = await instance!.copyFileToBuffer(name);
  await instance!.dropFile(name);
  return bytes;
}

/** Put downloaded bytes into DuckDB's in-memory file system (replacing a file of the same name). */
export async function registerBuffer(name: string, bytes: Uint8Array): Promise<void> {
  await connection();
  await instance!.registerFileBuffer(name, bytes);
}

// ---- safe SQL literals (all inputs are validated before they reach SQL) ----
export const num = (v: number) => {
  if (!Number.isFinite(v)) throw new Error('Invalid number');
  return String(v);
};
export const day = (v: string) => {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(v)) throw new Error('Invalid date');
  return `DATE '${v}'`;
};
export const str = (v: string) => `'${v.replace(/'/g, "''")}'`;
export const ints = (vs: number[]) => {
  if (!vs.length || !vs.every((v) => Number.isInteger(v))) throw new Error('Invalid list');
  return `(${vs.join(', ')})`;
};
