"""Step 2: build the small tables the calendar, anomaly, daily and grid endpoints read.

    python data_build/build_aggregates.py

Input : data/parquet/rows/year=*/part-0.parquet  (from build_parquet.py)
Output: data/parquet/daily_cells.parquet   counts per 0.1 deg cell x fire_date x pass x type x origin
        data/parquet/day_status.parquet    one line per fire_date: has_data, origin counts, flags
        data/weights.json                  weight table, uncertainty table, record dates
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from build_common import (DAYNIGHT_UNRELIABLE_FROM, DEFAULT_OUT, GRID_DEG, GRID_LAT0, GRID_LON0, LOCAL_END,
                          MIN_DAY_ROWS, RECORD_START, UNCERTAINTY, WEIGHTS, duckdb_connect)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--memory", default="3GB")
    ap.add_argument("--threads", type=int, default=None)
    a = ap.parse_args()

    pq = a.out / "parquet"
    rows = (pq / "rows" / "year=*" / "*.parquet").as_posix()
    tmp = a.out / "_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    con = duckdb_connect(a.memory, a.threads, tmp)
    con.execute(f"CREATE VIEW rows AS SELECT * FROM read_parquet('{rows}', hive_partitioning = false)")

    # ---------------- daily_cells
    t0 = time.perf_counter()
    out = pq / "daily_cells.parquet"
    con.execute(f"""
        COPY (
            SELECT fire_date, pass, type, origin, ci, cj,
                   CAST(round({GRID_LAT0} + ci * {GRID_DEG}, 1) AS DOUBLE) AS cell_lat,   -- south-west corner
                   CAST(round({GRID_LON0} + cj * {GRID_DEG}, 1) AS DOUBLE) AS cell_lon,
                   sum(weight)       AS count,
                   sum(frp * weight) AS frp_sum,
                   count(*)          AS n_rows
            FROM rows
            WHERE fire_date BETWEEN DATE '{RECORD_START}' AND DATE '{LOCAL_END}'
            GROUP BY ALL
            ORDER BY fire_date, ci, cj
        ) TO '{out.as_posix()}' (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 122880)
    """)
    n = con.execute(f"SELECT count(*) FROM '{out.as_posix()}'").fetchone()[0]
    print(f"daily_cells: {n:,} lines in {time.perf_counter() - t0:.0f} s")

    # ---------------- day_status (Australia-wide, all types: data availability does not depend on the box)
    t0 = time.perf_counter()
    out = pq / "day_status.parquet"
    con.execute(f"""
        COPY (
            WITH days AS (
                SELECT CAST(d AS DATE) AS fire_date
                FROM generate_series(DATE '{RECORD_START}', DATE '{LOCAL_END}', INTERVAL 1 DAY) g(d)
            ),
            s AS (
                SELECT fire_date,
                       count(*)                                        AS n_rows,
                       sum(weight)                                     AS count,
                       sum(CASE WHEN origin = 'real'    THEN weight END) AS count_real,
                       sum(CASE WHEN origin = 'ml'      THEN weight END) AS count_ml,
                       sum(CASE WHEN origin = 'gapfill' THEN weight END) AS count_gapfill
                FROM rows GROUP BY fire_date
            ),
            j AS (
                SELECT days.fire_date,
                       coalesce(s.n_rows, 0)        AS n_rows,
                       coalesce(s.count, 0)         AS count,
                       coalesce(s.count_real, 0)    AS count_real,
                       coalesce(s.count_ml, 0)      AS count_ml,
                       coalesce(s.count_gapfill, 0) AS count_gapfill
                FROM days LEFT JOIN s USING (fire_date)
            ),
            k AS (
                SELECT *,
                       lag(n_rows)  OVER (ORDER BY fire_date) AS prev_rows,
                       lead(n_rows) OVER (ORDER BY fire_date) AS next_rows
                FROM j
            )
            SELECT fire_date, n_rows, count, count_real, count_ml, count_gapfill,
                   CASE
                     WHEN n_rows = 0 THEN 'none'
                     WHEN n_rows < {MIN_DAY_ROWS} THEN 'partial'
                     WHEN prev_rows = 0 OR next_rows = 0 THEN 'partial'   -- next to an outage
                     ELSE 'full'
                   END AS has_data,
                   NOT (count_gapfill > 0 AND fire_date >= DATE '{DAYNIGHT_UNRELIABLE_FROM}') AS daynight_reliable
            FROM k
            ORDER BY fire_date
        ) TO '{out.as_posix()}' (FORMAT parquet)
    """)
    summary = dict(con.execute(f"SELECT has_data, count(*) FROM '{out.as_posix()}' GROUP BY 1").fetchall())
    print(f"day_status: {sum(summary.values()):,} days {summary} in {time.perf_counter() - t0:.0f} s")
    none_ranges = con.execute(f"""
        WITH n AS (SELECT fire_date, fire_date - CAST(row_number() OVER (ORDER BY fire_date) AS INTEGER) AS grp
                   FROM '{out.as_posix()}' WHERE has_data = 'none')
        SELECT min(fire_date), max(fire_date), count(*) FROM n GROUP BY grp ORDER BY 1
    """).fetchall()
    for lo, hi, c in none_ranges:
        print(f"  no data: {lo} -> {hi} ({c} days)")

    # ---------------- weights.json
    meta = {
        "record_start": RECORD_START, "local_end": LOCAL_END, "min_day_rows": MIN_DAY_ROWS,
        "grid": {"deg": GRID_DEG, "lat0": GRID_LAT0, "lon0": GRID_LON0},
        "weights": {"months": list(range(1, 13)), **WEIGHTS},
        "uncertainty": [{"origin": o, "from": f, "to": t, "value": v} for o, f, t, v in UNCERTAINTY],
        "daynight_unreliable_from": DAYNIGHT_UNRELIABLE_FROM,
        "known_gaps": [{"from": str(lo), "to": str(hi), "days": c} for lo, hi, c in none_ranges],
    }
    (a.out / "weights.json").write_text(json.dumps(meta, indent=1))
    print(f"weights.json written to {a.out}")


if __name__ == "__main__":
    main()
