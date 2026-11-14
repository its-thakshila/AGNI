// Reading the dataset's Parquet files in the browser with hyparquet (a ~21 KB pure-JS reader).
// Small files are downloaded whole; large files are read in parts: their index (footer) once, then
// only the row groups whose min/max statistics can match, all requests in parallel.

import { asyncBufferFromUrl, cachedAsyncBuffer, parquetMetadataAsync, parquetReadObjects } from 'hyparquet';
import type { AsyncBuffer, FileMetaData } from 'hyparquet';
import { ApiError } from '../errors';

/** Dates as 'YYYY-MM-DD' strings (compared as text), integers as plain numbers. */
const parsers = {
  dateFromDays: (days: number) => new Date(days * 86_400_000).toISOString().slice(0, 10),
};

export type Row = Record<string, unknown>;

/** A whole small Parquet file, checked for its "PAR1" markers, parsed into rows. */
export async function readWholeFile(url: string, columns?: string[]): Promise<Row[]> {
  let res: Response;
  try {
    res = await fetch(url);
  } catch (e) {
    throw new ApiError('NETWORK', String(e));
  }
  if (!res.ok) throw new ApiError('INTERNAL_ERROR', `Dataset file missing (HTTP ${res.status}).`, res.status);
  const buf = await res.arrayBuffer();
  const bytes = new Uint8Array(buf);
  // an error page or a cut-off download must not be used (or cached by the caller)
  const magic = (at: number) => String.fromCharCode(...bytes.subarray(at, at + 4)) === 'PAR1';
  if (bytes.length < 12 || !magic(0) || !magic(bytes.length - 4))
    throw new ApiError('INTERNAL_ERROR', 'A dataset file is damaged or incomplete.');
  const file: AsyncBuffer = { byteLength: buf.byteLength, slice: (s, e) => buf.slice(s, e) };
  return parquetReadObjects({ file, columns, parsers }) as Promise<Row[]>;
}

interface Remote { file: AsyncBuffer; metadata: FileMetaData }
const remotes = new Map<string, Promise<Remote>>();

/** Open a large remote file once per URL (URLs carry a version, so a cached one never goes stale). */
function openRemote(url: string): Promise<Remote> {
  let r = remotes.get(url);
  if (!r) {
    r = (async () => {
      try {
        const file = cachedAsyncBuffer(await asyncBufferFromUrl({ url }));
        return { file, metadata: await parquetMetadataAsync(file, { parsers }) };
      } catch (e) {
        throw e instanceof ApiError ? e : new ApiError('NETWORK', `Could not read a dataset file: ${String(e)}`);
      }
    })();
    remotes.set(url, r);
    r.catch(() => remotes.delete(url)); // retry on the next query
  }
  return r;
}

/** Keep a row group when the column's [min, max] statistics may contain a match. */
export type Prune = (min: unknown, max: unknown) => boolean;

/**
 * Rows of a large remote file. `prune` (column -> test on that column's row-group min/max) skips row
 * groups that cannot match; the remaining contiguous groups are read in parallel.
 */
export async function readRemote(url: string, columns: string[], prune: Record<string, Prune> = {}): Promise<Row[]> {
  const { file, metadata } = await openRemote(url);
  const ranges: [number, number][] = [];
  let start = 0;
  for (const group of metadata.row_groups) {
    const end = start + Number(group.num_rows);
    const keep = Object.entries(prune).every(([name, test]) => {
      const chunk = group.columns.find((c) => c.meta_data?.path_in_schema.join('.') === name);
      const stats = chunk?.meta_data?.statistics;
      return !stats || stats.min_value === undefined || stats.max_value === undefined || test(stats.min_value, stats.max_value);
    });
    if (keep) {
      const last = ranges[ranges.length - 1];
      if (last && last[1] === start) last[1] = end;
      else ranges.push([start, end]);
    }
    start = end;
  }
  try {
    const parts = await Promise.all(ranges.map(([rowStart, rowEnd]) =>
      parquetReadObjects({ file, metadata, columns, rowStart, rowEnd, parsers }) as Promise<Row[]>));
    return parts.flat();
  } catch (e) {
    throw new ApiError('NETWORK', `Could not read a dataset file: ${String(e)}`);
  }
}

export const num = (v: unknown) => (typeof v === 'bigint' ? Number(v) : (v as number) ?? 0);
export const text = (v: unknown) => (v instanceof Date ? v.toISOString().slice(0, 10) : v == null ? null : String(v));
