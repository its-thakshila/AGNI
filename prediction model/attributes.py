"""Stage-3 targets: per 0.1 deg cell x fire-day x pass means of the real VIIRS rows
(or, with fcst_config.TARGET_SOURCE = "unified", of all rows in unified_viirs_format_gapfilled.csv).

Reads data/train/VIIRS/viirs-snpp_<YEAR>_Australia.csv with DuckDB and writes
output/cache/viirs_cell_attributes.parquet with n, frp_mean, ti4_mean, ti5_mean and the
confidence shares sh_l / sh_n / sh_h. Fire-day and pass use the same rules as ml_pipeline/common.py
(local solar time = UTC + lon/15 h; pass D = 06-18 LST; fire_date = date(LST - 6 h)).

    python attributes.py            # skipped when the cache is newer than every VIIRS CSV
    python attributes.py --force
"""
from __future__ import annotations

import argparse
import logging
import time

import fcst_config as fc

log = logging.getLogger("attributes")


def build(force: bool = False):
    unified = fc.TARGET_SOURCE == "unified"
    if unified:
        files = [fc.UNIFIED_ROWS_CSV] if fc.UNIFIED_ROWS_CSV.exists() else []
    else:
        files = sorted(fc.VIIRS_DIR.glob(fc.mlc.VIIRS_NAME.format(year="*")))
    if not files:
        raise FileNotFoundError(f"no {'unified CSV ' + str(fc.UNIFIED_ROWS_CSV) if unified else 'VIIRS CSVs in ' + str(fc.VIIRS_DIR)}")
    out = fc.ATTR_PARQUET_UNIFIED if unified else fc.ATTR_PARQUET
    if not force and out.exists() and out.stat().st_mtime > max(f.stat().st_mtime for f in files):
        log.info("attributes up to date: %s", out)
        return out
    import duckdb
    out.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    lst = ", ".join("'" + f.as_posix() + "'" for f in files)
    g, la0, lo0 = fc.mlc.GRID_DEG, fc.mlc.GRID_LAT0, fc.mlc.GRID_LON0
    types = ",".join(str(t) for t in fc.FIRE_TYPES)
    con = duckdb.connect()
    con.execute(f"""
    COPY (
      WITH r AS (
        SELECT latitude, longitude, frp, bright_ti4, bright_ti5,
               lower(substr(trim(confidence), 1, 1)) AS conf,
               strptime(acq_date || ' ' || lpad(acq_time, 4, '0'), '%Y-%m-%d %H%M')
                 + to_microseconds(CAST(round(longitude / 15.0 * 3600e6) AS BIGINT)) AS lst
        FROM read_csv([{lst}], header = true, union_by_name = true,
                      types = {{'acq_time': 'VARCHAR', 'confidence': 'VARCHAR', 'version': 'VARCHAR',
                               'satellite': 'VARCHAR', 'daynight': 'VARCHAR'}})
        WHERE type IN ({types})
      )
      SELECT CAST(floor((latitude - ({la0})) / {g}) AS INTEGER) AS ci,
             CAST(floor((longitude - ({lo0})) / {g}) AS INTEGER) AS cj,
             CAST(lst - INTERVAL 6 HOUR AS DATE) AS fire_date,
             CASE WHEN hour(lst) >= 6 AND hour(lst) < 18 THEN 'D' ELSE 'N' END AS pass,
             count(*) AS n, avg(frp) AS frp_mean, avg(bright_ti4) AS ti4_mean, avg(bright_ti5) AS ti5_mean,
             avg(CAST(conf = 'l' AS DOUBLE)) AS sh_l, avg(CAST(conf = 'n' AS DOUBLE)) AS sh_n,
             avg(CAST(conf = 'h' AS DOUBLE)) AS sh_h
      FROM r GROUP BY ALL
    ) TO '{out.as_posix()}' (FORMAT PARQUET)""")
    con.execute(f"CREATE TEMP VIEW a AS SELECT * FROM read_parquet('{out.as_posix()}')")
    n, rows = con.execute("SELECT count(*), sum(n) FROM a").fetchone()
    # agreement with the real rows in daily_cells.parquet
    chk = con.execute(f"""
        SELECT sum(abs(coalesce(a.n, 0) - coalesce(c.count, 0))) / sum(coalesce(c.count, 0))
        FROM (SELECT * FROM a) a FULL JOIN
             (SELECT ci, cj, fire_date, pass, sum({'n_rows' if unified else 'count'}) AS count
              FROM read_parquet('{fc.CELLS_PARQUET.as_posix()}')
              WHERE {'TRUE' if unified else "origin = 'real'"} AND type IN ({types}) GROUP BY ALL) c
        USING (ci, cj, fire_date, pass)""").fetchone()[0]
    con.close()
    log.info("attributes: %d cell-day-passes, %d rows, mismatch vs daily_cells %.3f%%, %.0f s -> %s",
             n, rows, 100 * (chk or 0), time.perf_counter() - t0, out)
    return out


def load():
    import pandas as pd
    a = pd.read_parquet(build())
    a["fire_date"] = pd.to_datetime(a["fire_date"])
    return a


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true")
    build(ap.parse_args().force)
