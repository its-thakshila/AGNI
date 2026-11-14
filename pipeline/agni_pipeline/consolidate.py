"""Fold the days added after the CSV (live/*) into the main dataset (hist/*).

This is the "add it to the CSV" step. Finished days (not provisional) move from the live
monthly files into hist/, the manifest's record end moves forward, and optionally the full
processed CSV is re-exported in the 15-column format.

Usage:
  python -m agni_pipeline.consolidate --store r2:real --export-csv csv/agni_processed.csv
"""

from __future__ import annotations

import argparse
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb

from .build_hist import write_hist
from .common import CELLS_SELECT, ROW_COLUMNS, ROWS_TABLE_SQL, VIIRS_COLUMNS
from .storage import open_store, read_json, write_json


def consolidate(store, export_csv: str | None = None, keep_recent_days: int = 3) -> dict:
    manifest = read_json(store, "manifest.json", None)
    if manifest is None:
        raise SystemExit("manifest.json not found.")
    today = datetime.now(UTC).date()
    cutoff = today - timedelta(days=keep_recent_days)
    final = sorted(d for d, s in manifest["days"].items()
                   if date.fromisoformat(d) < cutoff and s.get("flag") != "provisional")
    if not final:
        print("No finished live days to consolidate.")
        return manifest
    # only a continuous run after the CSV can be folded in, so the record has no holes
    run, expected = [], date.fromisoformat(manifest["local_end"]) + timedelta(days=1)
    for d in final:
        if date.fromisoformat(d) != expected:
            break
        run.append(d)
        expected += timedelta(days=1)
    if not run:
        print("The first day after the CSV is not finished yet; nothing to consolidate.")
        return manifest

    cols = ", ".join(f'"{c}"' for c in ROW_COLUMNS)
    years = sorted({int(d[:4]) for d in run})
    legacy = "row_years" not in manifest  # older layout: all detections in hist/rows.parquet
    with tempfile.TemporaryDirectory() as tmp, duckdb.connect() as db:
        folder = Path(tmp)

        def fetch(key: str) -> str | None:
            data = store.read(key)
            if data is None:
                return None
            path = folder / key.replace("/", "_")
            path.write_bytes(data)
            return str(path)

        live_rows = [p for p in (fetch(f"live/rows/{m}.parquet") for m in manifest["live_months"]) if p]
        db.execute(ROWS_TABLE_SQL.format(name="new_rows"))
        if live_rows:
            db.execute(f"INSERT INTO new_rows SELECT {cols} FROM read_parquet(?) WHERE fire_date::VARCHAR IN (SELECT unnest(?))",
                       [live_rows, run])

        # detections: only the years that receive days are downloaded and rewritten
        if legacy:
            old = fetch("hist/rows.parquet")
            years = None  # split the whole old file into year files once
            sources = [old] if old else []
        else:
            sources = [p for p in (fetch(f"hist/rows/{y}.parquet") for y in years) if p]
        db.execute(ROWS_TABLE_SQL.format(name="rows"))
        if sources:
            db.execute(f"INSERT INTO rows SELECT {cols} FROM read_parquet(?)", [sources])
        db.execute("INSERT INTO rows SELECT * FROM new_rows")
        db.execute("COPY rows TO (?) (FORMAT PARQUET, PARQUET_VERSION V1)", [str(folder / "rows.parquet")])

        # cells and status are small: add the new days to them
        db.execute(f"""COPY (SELECT * FROM read_parquet(?) UNION ALL BY NAME {CELLS_SELECT.format(source='new_rows')})
                       TO (?) (FORMAT PARQUET, PARQUET_VERSION V1)""", [fetch("hist/cells.parquet"), str(folder / "cells.parquet")])
        db.execute("CREATE TABLE status AS SELECT * FROM read_parquet(?)", [fetch("hist/day_status.parquet")])
        db.executemany("INSERT INTO status VALUES (?::DATE, ?, ?, ?)",
                       [[d, manifest["days"][d]["has_data"], manifest["days"][d]["daynight_reliable"], manifest["days"][d]["flag"]] for d in run])
        db.execute("COPY status TO (?) (FORMAT PARQUET, PARQUET_VERSION V1)", [str(folder / "day_status.parquet")])
        info = write_hist(store, folder, db, years=years)
        if export_csv:
            # the full CSV needs every year: download them all only when an export is asked for
            all_years = [p for p in (fetch(k) for k in store.list("hist/rows/")) if p]
            db.execute(f"COPY (SELECT {', '.join(VIIRS_COLUMNS)} FROM read_parquet(?) ORDER BY acq_date, acq_time) TO (?) (HEADER)",
                       [all_years, str(folder / "export.csv")])
            store.write(export_csv, (folder / "export.csv").read_bytes())

    for d in run:
        del manifest["days"][d]
    row_years = {} if legacy else dict(manifest["row_years"])
    row_years.update(info["row_years"])
    manifest.update(record_start=info["record_start"], local_end=info["local_end"], known_gaps=info["known_gaps"],
                    row_years=dict(sorted(row_years.items())), updated_at=datetime.now(UTC).isoformat(timespec="seconds"))
    # live month files still hold the consolidated days: drop whole months now in hist, and
    # rewrite partly consolidated months without those days (otherwise they'd count twice)
    still_live = {d[:7] for d in manifest["days"]}
    with tempfile.TemporaryDirectory() as tmp, duckdb.connect() as db:
        for month in manifest["live_months"]:
            if month not in still_live:
                store.delete(f"live/rows/{month}.parquet")
                store.delete(f"live/cells/{month}.parquet")
                continue
            moved = [d for d in run if d.startswith(month)]
            data = store.read(f"live/rows/{month}.parquet") if moved else None
            if not data:
                continue
            src, rows_out, cells_out = (Path(tmp) / f"{month}-{n}.parquet" for n in ("src", "rows", "cells"))
            src.write_bytes(data)
            db.execute(f"CREATE OR REPLACE TABLE m AS SELECT * FROM read_parquet(?) WHERE fire_date::VARCHAR NOT IN (SELECT unnest(?))",
                       [str(src), moved])
            db.execute("COPY (SELECT * FROM m ORDER BY acq_date, acq_time) TO (?) (FORMAT PARQUET, PARQUET_VERSION V1)", [str(rows_out)])
            db.execute(f"COPY ({CELLS_SELECT.format(source='m')} ORDER BY fire_date, cj, ci) TO (?) (FORMAT PARQUET, PARQUET_VERSION V1)", [str(cells_out)])
            store.write(f"live/rows/{month}.parquet", rows_out.read_bytes())
            store.write(f"live/cells/{month}.parquet", cells_out.read_bytes())
    manifest["live_months"] = sorted(still_live)
    write_json(store, "manifest.json", manifest)
    print(f"Consolidated {len(run)} day(s): the record now ends {manifest['local_end']}.")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store", required=True)
    parser.add_argument("--export-csv", help="store key for a full CSV export, e.g. csv/agni_processed.csv")
    args = parser.parse_args()
    consolidate(open_store(args.store), args.export_csv)


if __name__ == "__main__":
    main()
