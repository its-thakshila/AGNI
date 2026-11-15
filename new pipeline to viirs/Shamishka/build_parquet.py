"""Step 1: split the unified CSV into one Parquet file per year, with the columns the API needs.

    python scripts/build_parquet.py
    python scripts/build_parquet.py --csv path/to/unified_viirs_format_gapfilled.csv --memory 3GB

Input : unified_viirs_format_gapfilled.csv (about 30.9 M rows, about 3 GB)
Output: data/parquet/rows/year=YYYY/part-0.parquet   (YYYY = year of fire_date)

Runs in two passes so it fits in 8 GB of RAM:
  pass 1  CSV -> one unsorted staging Parquet with all derived columns (reads the CSV once)
  pass 2  staging -> one sorted file per year (sorting one year at a time keeps memory low)
"""
from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path

from build_common import (CSV_COLUMNS, DAY_START_HOUR, DEFAULT_CSV, DEFAULT_OUT, GRID_DEG, GRID_LAT0,
                          GRID_LON0, duckdb_connect, weights_values_sql)


def derived_select(csv: Path) -> str:
    cols = "{" + ", ".join(f"'{k}': '{v}'" for k, v in CSV_COLUMNS.items()) + "}"
    return f"""
    WITH src AS (
        SELECT * FROM read_csv('{csv.as_posix()}', header = true, columns = {cols},
                               dateformat = '%Y-%m-%d', parallel = true)
    ),
    t AS (
        SELECT *,
            lpad(trim(acq_time), 4, '0') AS hhmm
        FROM src
    ),
    t2 AS (
        SELECT * EXCLUDE (acq_time, hhmm),
            hhmm AS acq_time,
            CAST(acq_date AS TIMESTAMP)
                + to_minutes(CAST(substr(hhmm, 1, 2) AS BIGINT) * 60 + CAST(substr(hhmm, 3, 2) AS BIGINT)) AS utc
        FROM t
    ),
    t3 AS (
        SELECT *,
            -- local solar time = UTC + longitude / 15 hours (microsecond precision, like pandas)
            utc + to_microseconds(CAST(round(longitude * 240000000) AS BIGINT)) AS lst
        FROM t2
    ),
    t4 AS (
        SELECT *,
            CAST(hour(lst) + minute(lst) / 60.0 AS FLOAT) AS lst_hour,
            CAST(lst - INTERVAL {DAY_START_HOUR} HOUR AS DATE) AS fire_date
        FROM t3
    )
    SELECT
        -- the 15 VIIRS columns, in the CSV order
        t4.latitude, t4.longitude, t4.bright_ti4,
        round(t4.scan, 2) AS scan, round(t4.track, 2) AS track,
        t4.acq_date, t4.acq_time, t4.satellite, t4.instrument, t4.confidence, t4.version,
        t4.bright_ti5, t4.frp, t4.daynight, t4.type,
        -- derived
        t4.utc, t4.lst_hour, t4.fire_date,
        CASE WHEN t4.lst_hour >= 6 AND t4.lst_hour < 18 THEN 'D' ELSE 'N' END AS pass,
        CASE WHEN t4.version LIKE 'ML-%-gapfill' THEN 'gapfill'
             WHEN t4.version LIKE 'ML-%' THEN 'ml'
             ELSE 'real' END AS origin,
        CAST(floor((t4.latitude - ({GRID_LAT0})) / {GRID_DEG}) AS INTEGER) AS ci,
        CAST(floor((t4.longitude - ({GRID_LON0})) / {GRID_DEG}) AS INTEGER) AS cj
    FROM t4
    """


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--memory", default="3GB", help="DuckDB memory limit (default 3GB, for an 8 GB laptop)")
    ap.add_argument("--threads", type=int, default=None)
    ap.add_argument("--keep-staging", action="store_true", help="keep the unsorted staging file")
    a = ap.parse_args()

    if not a.csv.exists():
        raise SystemExit(f"CSV not found: {a.csv}\nRebuild it first: ml_pipeline -> python unify.py && python gapfill.py")

    rows_dir = a.out / "parquet" / "rows"
    tmp = a.out / "_tmp"
    staging = tmp / "staging.parquet"
    if rows_dir.exists():
        shutil.rmtree(rows_dir)
    rows_dir.mkdir(parents=True)
    tmp.mkdir(parents=True, exist_ok=True)
    con = duckdb_connect(a.memory, a.threads, tmp)

    # ---------------- pass 1: CSV -> staging (all derived columns, weight added below)
    t0 = time.perf_counter()
    print(f"pass 1: reading {a.csv} ...")
    con.execute(f"""
        COPY (
            WITH d AS ({derived_select(a.csv)}),
            w(month, pass, w) AS ({weights_values_sql()})
            SELECT d.*,
                   CAST(CASE WHEN d.origin = 'real' THEN 1.0 ELSE w.w END AS DOUBLE) AS weight
            FROM d LEFT JOIN w ON w.month = month(d.fire_date) AND w.pass = d.pass
        ) TO '{staging.as_posix()}' (FORMAT parquet, COMPRESSION zstd)
    """)
    n = con.execute(f"SELECT count(*) FROM '{staging.as_posix()}'").fetchone()[0]
    print(f"pass 1 done: {n:,} rows in {time.perf_counter() - t0:.0f} s")

    # ---------------- pass 2: one sorted file per year of fire_date
    years = [r[0] for r in con.execute(
        f"SELECT DISTINCT year(fire_date) FROM '{staging.as_posix()}' ORDER BY 1").fetchall()]
    for y in years:
        t1 = time.perf_counter()
        d = rows_dir / f"year={y}"
        d.mkdir()
        con.execute(f"""
            COPY (
                SELECT * FROM '{staging.as_posix()}'
                WHERE year(fire_date) = {y}
                ORDER BY fire_date, latitude, longitude
            ) TO '{(d / 'part-0.parquet').as_posix()}'
            (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 122880)
        """)
        ny = con.execute(f"SELECT count(*) FROM '{(d / 'part-0.parquet').as_posix()}'").fetchone()[0]
        print(f"  {y}: {ny:>10,} rows  ({time.perf_counter() - t1:.0f} s)")

    if not a.keep_staging:
        shutil.rmtree(tmp, ignore_errors=True)
    size = sum(p.stat().st_size for p in rows_dir.rglob("*.parquet")) / 1e6
    print(f"done: {len(years)} years, {size:,.0f} MB in {rows_dir}  ({time.perf_counter() - t0:.0f} s total)")


if __name__ == "__main__":
    main()
