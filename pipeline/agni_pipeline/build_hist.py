"""Build the historical browser files (hist/*) and a fresh manifest from the processed CSV.

Uses the backend's builder (backend/scripts/build_viirs.py) for the CSV -> Parquet step, then
adds a per-month summary and sorts the files so the browser downloads only what a query needs.

Usage:
  python -m agni_pipeline.build_hist --store local:../frontend/public/data/mock --synthetic
  python -m agni_pipeline.build_hist --store r2:real --csv processed.csv --status day_status.csv
"""

from __future__ import annotations

import argparse
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from .common import BBOX, CELLS_MONTH_SELECT, build_from_parquet, load_backend_builder
from .storage import open_store, read_json, write_json

PERIODS = [
    {"from": "2000-11-01", "to": "2002-07-03", "origin": "ml", "label": "Harmonized from MODIS Terra by Harmonic", "uncertainty": 0.15},
    {"from": "2002-07-04", "to": "2011-12-31", "origin": "ml", "label": "Harmonized from MODIS Terra + Aqua by Harmonic", "uncertainty": 0.11},
    {"from": "2012-01-01", "to": None, "origin": "real", "label": "VIIRS S-NPP observations", "uncertainty": 0.0},
]


def write_hist(store, folder: Path, db, years: list[int] | None = None) -> dict:
    """Write the history files to the store.

    `folder` holds cells.parquet, day_status.parquet and rows.parquet. The detections are stored
    one file per year (hist/rows/<year>.parquet), so adding days later rewrites only that year.
    years=None writes every year in rows.parquet (a full build, which also removes year files
    that no longer exist); otherwise only those years (rows.parquet must hold them in full).
    Returns the coverage info and {year: version} of the row files written.
    """
    out = folder / "out"
    out.mkdir(exist_ok=True)
    cells, rows, status = (str(folder / n) for n in ("cells.parquet", "rows.parquet", "day_status.parquet"))
    # sorted so that date filters (daily/map) and box filters (calendar) can skip most of each file
    db.execute("COPY (SELECT * FROM read_parquet(?) ORDER BY fire_date, cj, ci) TO (?) (FORMAT PARQUET, PARQUET_VERSION V1, ROW_GROUP_SIZE 50000)",
               [cells, str(out / "cells.parquet")])
    db.execute(f"COPY ({CELLS_MONTH_SELECT.format(source='read_parquet(?)')} ORDER BY cj, ci, year, month) "
               "TO (?) (FORMAT PARQUET, PARQUET_VERSION V1, ROW_GROUP_SIZE 50000)", [cells, str(out / "cells_month.parquet")])
    db.execute("COPY (SELECT * FROM read_parquet(?) ORDER BY fire_date) TO (?) (FORMAT PARQUET, PARQUET_VERSION V1)", [status, str(out / "day_status.parquet")])
    # all-Australia totals per day (well under 1 MB): the website loads this whole and answers the
    # default view (calendar, trends, comparisons for all of Australia) without scanning cells
    min_lon, min_lat, max_lon, max_lat = BBOX
    db.execute(f"""COPY (SELECT fire_date, "pass", type, origin, SUM(count) AS count, SUM(frp_sum) AS frp_sum, SUM(n_rows) AS n_rows
                         FROM read_parquet(?) WHERE cell_lat BETWEEN {min_lat} AND {max_lat} AND cell_lon BETWEEN {min_lon} AND {max_lon}
                         GROUP BY ALL ORDER BY fire_date) TO (?) (FORMAT PARQUET, PARQUET_VERSION V1)""",
               [cells, str(out / "day_totals.parquet")])
    for name in ("cells", "cells_month", "day_status", "day_totals"):
        store.write(f"hist/{name}.parquet", (out / f"{name}.parquet").read_bytes())

    present = [y for (y,) in db.execute("SELECT DISTINCT year(fire_date)::INT FROM read_parquet(?) ORDER BY 1", [rows]).fetchall()]
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    row_years = {}
    for year in (present if years is None else [y for y in years if y in present]):
        path = out / f"rows_{year}.parquet"
        db.execute("COPY (SELECT * FROM read_parquet(?) WHERE year(fire_date) = ? ORDER BY fire_date, cj, ci) "
                   "TO (?) (FORMAT PARQUET, PARQUET_VERSION V1, ROW_GROUP_SIZE 50000)", [rows, year, str(path)])
        store.write(f"hist/rows/{year}.parquet", path.read_bytes())
        path.unlink()
        row_years[str(year)] = stamp
    if years is None:
        for key in store.list("hist/rows/"):
            if key.removeprefix("hist/rows/").removesuffix(".parquet") not in row_years:
                store.delete(key)
    store.delete("hist/rows.parquet")  # the older single-file layout

    first, last = db.execute(
        "SELECT min(fire_date), max(fire_date) FROM read_parquet(?) WHERE has_data <> 'none'", [status]).fetchone()
    gaps = db.execute("""
        SELECT min(fire_date)::VARCHAR, max(fire_date)::VARCHAR FROM (
            SELECT fire_date, fire_date - (row_number() OVER (ORDER BY fire_date))::INT AS grp
            FROM read_parquet(?) WHERE has_data = 'none') GROUP BY grp ORDER BY 1""", [status]).fetchall()
    return {"record_start": str(first), "local_end": str(last), "row_years": row_years, "totals": True,
            "known_gaps": [{"from": a, "to": b, "reason": "No satellite data"} for a, b in gaps]}


def build(store, csv_path: Path | None, status_path: Path | None, dataset: str, parquet_files: list[Path] | None = None) -> dict:
    builder = load_backend_builder()
    with tempfile.TemporaryDirectory() as tmp, duckdb.connect() as db:
        folder = Path(tmp)
        if parquet_files:
            build_from_parquet(db, [str(p) for p in parquet_files], str(status_path) if status_path else None,
                               folder, (builder.DAY_WEIGHTS, builder.NIGHT_WEIGHTS))
        else:
            if csv_path is None:
                csv_path, status_path = builder.generate_mock(folder)
            builder.build(Path(csv_path), Path(status_path), folder)
        info = write_hist(store, folder, db)

    previous = read_json(store, "manifest.json", {})
    manifest = {
        "version": 1, "dataset": dataset, **info,
        "latest_fire_date": info["local_end"], "periods": PERIODS,
        "days": {}, "live_months": [], "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    # keep days already added after the (new) end of the CSV
    for day, entry in previous.get("days", {}).items():
        if day > info["local_end"]:
            manifest["days"][day] = entry
    manifest["live_months"] = sorted({d[:7] for d in manifest["days"]})
    with_data = [d for d, s in manifest["days"].items() if s["has_data"] != "none"]
    manifest["latest_fire_date"] = max([info["local_end"], *with_data])
    write_json(store, "manifest.json", manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store", required=True, help="local:<folder> or r2:<prefix>")
    parser.add_argument("--synthetic", action="store_true", help="use the backend's synthetic mock dataset")
    parser.add_argument("--csv", type=Path, help="processed CSV (15 VIIRS columns)")
    parser.add_argument("--status", type=Path, help="day status CSV: fire_date,has_data,daynight_reliable,flag")
    parser.add_argument("--csv-key", help="read the processed CSV from this key in the store (e.g. csv/processed.csv)")
    parser.add_argument("--status-key", help="read the day status (CSV or Parquet) from this key in the store; "
                        "if missing, the same name with the other extension is tried, then coverage is derived")
    parser.add_argument("--parquet", type=Path, help="folder of per-year Parquet files (15 CSV columns)")
    parser.add_argument("--parquet-prefix", help="read every .parquet under this prefix in the store (e.g. source/)")
    args = parser.parse_args()
    from_files = bool(args.csv) and bool(args.status)
    from_store = bool(args.csv_key) and bool(args.status_key)
    from_parquet = bool(args.parquet) or bool(args.parquet_prefix)
    if [args.synthetic, from_files, from_store, from_parquet].count(True) != 1:
        parser.error("Use one of: --synthetic, --csv + --status, --csv-key + --status-key, --parquet or --parquet-prefix.")
    store = open_store(args.store)
    dataset = "mock" if args.synthetic else "real"
    with tempfile.TemporaryDirectory() as tmp:
        csv_path, status_path = args.csv, args.status
        if from_parquet:
            if args.parquet:
                files = sorted(args.parquet.glob("*.parquet"))
            else:
                files = []
                for key in (k for k in store.list(args.parquet_prefix) if k.endswith(".parquet")):
                    path = Path(tmp) / key.replace("/", "_")
                    path.write_bytes(store.read(key))
                    files.append(path)
            if not files:
                raise SystemExit("No .parquet files found.")
            if args.status_key:
                stem = args.status_key.rsplit(".", 1)[0]
                for key in dict.fromkeys([args.status_key, stem + ".parquet", stem + ".csv"]):
                    data = store.read(key)
                    if data is not None:
                        status_path = Path(tmp) / Path(key).name
                        status_path.write_bytes(data)
                        print(f"Day status from {key}")
                        break
                else:
                    print(f"{args.status_key} not found: coverage will be derived from the detections")
            print(f"Reading {len(files)} Parquet file(s)" + ("" if status_path else " (no status file: coverage derived from detections)"))
            manifest = build(store, None, status_path, dataset, parquet_files=files)
            print(f"{store}: {dataset} data {manifest['record_start']} to {manifest['local_end']}, "
                  f"{len(manifest['days'])} live day(s) kept")
            return
        if from_store:
            csv_path, status_path = Path(tmp) / "processed.csv", Path(tmp) / "day_status.csv"
            for key, path in ((args.csv_key, csv_path), (args.status_key, status_path)):
                data = store.read(key)
                if data is None:
                    raise SystemExit(f"{store}: {key} not found. Upload it first.")
                path.write_bytes(data)
        manifest = build(store, csv_path, status_path, dataset)
    print(f"{store}: {dataset} data {manifest['record_start']} to {manifest['local_end']}, "
          f"{len(manifest['days'])} live day(s) kept")


if __name__ == "__main__":
    main()
