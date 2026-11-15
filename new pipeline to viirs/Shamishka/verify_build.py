"""Step 3: check the Parquet build against the source CSVs. Exit code 0 = all checks passed.

    python scripts/verify_build.py
    python scripts/verify_build.py --skip-csv-count     # skip the slow full re-read of the 3 GB CSV
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from build_common import (CSV_COLUMNS, DEFAULT_CSV, DEFAULT_DAILY_CSV, DEFAULT_OUT, LOCAL_END, RECORD_START,
                          duckdb_connect)

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = ""):
    results.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  —  {detail}" if detail else ""))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    ap.add_argument("--daily-csv", type=Path, default=DEFAULT_DAILY_CSV)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--memory", default="3GB")
    ap.add_argument("--skip-csv-count", action="store_true")
    ap.add_argument("--tolerance", type=float, default=0.01, help="weighted-total tolerance per model year")
    a = ap.parse_args()

    pq = a.out / "parquet"
    tmp = a.out / "_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    con = duckdb_connect(a.memory, None, tmp)
    con.execute(f"CREATE VIEW rows AS SELECT * FROM read_parquet('{(pq / 'rows' / 'year=*' / '*.parquet').as_posix()}', hive_partitioning = true)")
    con.execute(f"CREATE VIEW cells AS SELECT * FROM '{(pq / 'daily_cells.parquet').as_posix()}'")
    con.execute(f"CREATE VIEW status AS SELECT * FROM '{(pq / 'day_status.parquet').as_posix()}'")

    # 1. row counts per UTC year: CSV vs Parquet
    pq_years = dict(con.execute("SELECT year(acq_date), count(*) FROM rows GROUP BY 1").fetchall())
    if a.skip_csv_count:
        print("[SKIP] row counts vs CSV")
    else:
        cols = "{" + ", ".join(f"'{k}': '{v}'" for k, v in CSV_COLUMNS.items()) + "}"
        csv_years = dict(con.execute(f"""
            SELECT year(acq_date), count(*) FROM read_csv('{a.csv.as_posix()}', header = true, columns = {cols})
            GROUP BY 1""").fetchall())
        diff = {y: (csv_years.get(y, 0), pq_years.get(y, 0)) for y in set(csv_years) | set(pq_years)
                if csv_years.get(y, 0) != pq_years.get(y, 0)}
        check("row counts per year match the CSV", not diff,
              f"{sum(csv_years.values()):,} rows" if not diff else f"differences {diff}")

    # 2. partitions: every row is in the folder of its fire_date year
    bad = con.execute("SELECT count(*) FROM rows WHERE year(fire_date) <> year").fetchone()[0]
    check("each row is in the year folder of its fire_date", bad == 0, f"{bad} misplaced" if bad else "")

    # 3. column sanity
    q = con.execute("""
        SELECT count(*) FILTER (WHERE fire_date IS NULL OR utc IS NULL OR pass IS NULL),
               count(*) FILTER (WHERE length(acq_time) <> 4),
               count(*) FILTER (WHERE origin = 'real' AND weight <> 1.0),
               count(*) FILTER (WHERE origin <> 'real' AND (weight < 1.0 OR weight > 1.7 OR weight IS NULL)),
               count(*) FILTER (WHERE origin = 'real' AND version NOT IN ('2', '2.0')),
               count(*) FILTER (WHERE pass <> daynight),
               count(*)
        FROM rows""").fetchone()
    nulls, badtime, realw, mlw, badver, passdiff, total = q
    check("no missing fire_date / utc / pass", nulls == 0, f"{nulls} rows" if nulls else "")
    check("acq_time always 4 characters", badtime == 0, f"{badtime} rows" if badtime else "")
    check("real rows have weight 1.0", realw == 0, f"{realw} rows" if realw else "")
    check("model rows have weight 1.0-1.7", mlw == 0, f"{mlw} rows" if mlw else "")
    check("'real' rows all have version 2", badver == 0,
          f"{badver} rows with an unexpected version" if badver else "")
    share = passdiff / total if total else 0
    check("pass agrees with daynight in >= 97% of rows", share <= 0.03, f"{100 * (1 - share):.2f}% agree")
    vers = con.execute("SELECT version, origin, count(*) FROM rows GROUP BY 1, 2 ORDER BY 3 DESC").fetchall()
    print("       versions:", ", ".join(f"{v}->{o}: {n:,}" for v, o, n in vers))

    # 4. weighted totals per fire_date year vs the pipeline's daily file (model years: the main check of the weights)
    if a.daily_csv.exists():
        d = dict(con.execute(f"""
            SELECT year(CAST(fire_date AS DATE)), sum(viirs_n) FROM read_csv('{a.daily_csv.as_posix()}', header = true)
            WHERE CAST(fire_date AS DATE) BETWEEN DATE '{RECORD_START}' AND DATE '{LOCAL_END}' GROUP BY 1""").fetchall())
        p = dict(con.execute("SELECT year(fire_date), sum(weight) FROM rows GROUP BY 1").fetchall())
        model_years = sorted(y for y, in con.execute("SELECT DISTINCT year(fire_date) FROM rows WHERE origin = 'ml'").fetchall())
        worst = []
        for y in model_years:
            if d.get(y):
                worst.append((abs(float(p.get(y, 0)) / float(d[y]) - 1), y))
        worst.sort(reverse=True)
        ok = bool(worst) and worst[0][0] <= a.tolerance
        check(f"weighted totals match unified_daily_gapfilled within {100 * a.tolerance:.0f}% (model years)", ok,
              f"largest difference {100 * worst[0][0]:.2f}% in {worst[0][1]}" if worst else "no model years found")
        for y in sorted(set(d) | set(p)):
            print(f"       {y}: parquet {p.get(y, 0):>12,.0f}   daily csv {d.get(y, 0):>12,.0f}"
                  f"   ratio {p.get(y, 0) / d[y] if d.get(y) else float('nan'):.3f}")
    else:
        print(f"[SKIP] weighted totals: {a.daily_csv} not found")

    # 5. aggregates add up to the rows
    r = con.execute(f"SELECT sum(weight), count(*) FROM rows WHERE fire_date BETWEEN DATE '{RECORD_START}' AND DATE '{LOCAL_END}'").fetchone()
    c = con.execute("SELECT sum(count), sum(n_rows) FROM cells").fetchone()
    check("daily_cells totals equal the rows", abs(r[0] - c[0]) < 1e-6 * max(r[0], 1) and r[1] == c[1],
          f"rows {r[1]:,} / cells {c[1]:,}")
    s = con.execute("SELECT count(*), min(fire_date), max(fire_date), sum(n_rows) FROM status").fetchone()
    check("day_status has one line per day of the record", s[1].isoformat() == RECORD_START and
          s[2].isoformat() == LOCAL_END and s[3] == r[1], f"{s[0]:,} days, {s[1]} -> {s[2]}")

    # 6. known MODIS outages show as 'none'
    for lo, hi in (("2001-06-17", "2001-07-02"), ("2002-03-21", "2002-03-27")):
        n_none, n = con.execute(f"""SELECT count(*) FILTER (WHERE has_data = 'none'), count(*) FROM status
                                   WHERE fire_date BETWEEN DATE '{lo}' AND DATE '{hi}'""").fetchone()
        if n:
            check(f"outage {lo} -> {hi} is 'none'", n_none >= n - 2, f"{n_none} of {n} days")

    # 7. speed: a 2019 box query
    t0 = time.perf_counter()
    con.execute("""SELECT fire_date, sum(weight) FROM rows WHERE year = 2019
                   AND fire_date BETWEEN DATE '2019-11-01' AND DATE '2019-11-30'
                   AND latitude BETWEEN -37 AND -30 AND longitude BETWEEN 148 AND 153 AND type = 0
                   GROUP BY 1""").fetchall()
    dt = time.perf_counter() - t0
    check("2019 box + month query under 1 s", dt < 1.0, f"{dt:.2f} s")

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
