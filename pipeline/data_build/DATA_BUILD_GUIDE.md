# Data Build Guide — splitting the unified CSV for the API

For: the developer preparing the backend data · Backend architecture v2, build steps 0–1 · API contract 1.1

## What this does and why

The harmonized record is one CSV of about **30.9 million rows (about 3 GB)**. The API can't query it directly: a CSV has no index, so every request would read the whole file (10–30 s).

This job turns it, once, into files the API can query in under a second:

```
unified_viirs_format_gapfilled.csv  (3 GB)
        │
        ▼  build_parquet.py
data/parquet/rows/year=2000/part-0.parquet      every detection, split by year,
data/parquet/rows/year=2001/part-0.parquet      with the extra columns the API needs
...
data/parquet/rows/year=2025/part-0.parquet
        │
        ▼  build_aggregates.py
data/parquet/daily_cells.parquet    totals per 0.1° cell × day × pass  → calendar, daily, anomaly, map grid
data/parquet/day_status.parquet     one line per day: is there data?   → has_data, "no data" days
data/weights.json                   weight and uncertainty tables      → /api/meta
        │
        ▼  verify_build.py
14 checks, all must PASS
```

The CSV stays as it is. It remains the master copy. Nothing in `ml_pipeline` is changed.

The scripts are ready to run. They were tested on a 2.75-million-row synthetic file in the same format: all 14 checks passed, and every row's fire-day and day/night pass matched the pipeline's own pandas rules exactly.

---

## 1. Before you start

**Requirements**

- Python 3.10+
- `pip install duckdb pyarrow pandas`
- About **6 GB free disk**: the result is about 1 GB, and DuckDB uses temporary space while sorting.
- Close Chrome and Edge. The scripts are capped at 3 GB of RAM, but an 8 GB laptop with browsers open has only about 1.3 GB free.

**Folder layout.** The scripts live in `pipeline/data_build/` and read the CSV the ML pipeline writes:

```
pipeline\
├── harmonic\ml_pipeline\output\unified\unified_viirs_format_gapfilled.csv
├── harmonic\ml_pipeline\output\unified\unified_daily_gapfilled.csv
└── data_build\  build_common.py  build_parquet.py  build_aggregates.py  verify_build.py
                 data\   ← output (git-ignored)
```

Run every command below from the `pipeline/` folder.

If your files are elsewhere, every script takes `--csv`, `--daily-csv` and `--out`.

---

## 2. Step 0 — rebuild the unified CSV (only if it's missing)

The 3 GB file is not currently on disk. Recreate it from the pipeline:

```bash
cd harmonic/ml_pipeline
python unify.py      # writes output/unified/unified_viirs_format.csv + unified_daily.csv
python gapfill.py    # writes output/unified/unified_viirs_format_gapfilled.csv + unified_daily_gapfilled.csv
```

About 20 minutes.

> **Use the `_gapfilled` files.** In the plain `unified_viirs_format.csv`, VIIRS outage days have no rows, so they would look like days with zero fires.

---

## 3. Step 1 — split the CSV into yearly Parquet files

```bash
python data_build/build_parquet.py
```

Options: `--memory 3GB` (lower to `2GB` if the laptop struggles) · `--csv <path>` · `--out <folder>`.

**What it does**

1. **Pass 1** reads the CSV once and adds the derived columns (below) into a temporary unsorted file, `data/_tmp/staging.parquet`.
2. **Pass 2** writes one file per year, sorted by `fire_date`, then latitude, then longitude. Sorting lets DuckDB skip most of a file when the API asks for one box and one month. Sorting one year at a time keeps memory low.
3. Deletes the temporary file (`--keep-staging` keeps it).

Expect a few minutes and a printed row count per year.

**The year is the year of `fire_date`, not of `acq_date`.** All API dates are local fire-days. A few night-pass rows have a UTC `acq_date` of 1 January but belong to fire-day 31 December. They go in the previous year's file, so a request for one year reads exactly one file.

### Columns in `rows/year=YYYY/part-0.parquet`

The 15 CSV columns, unchanged except `scan` and `track` rounded to 2 decimals (model rows contain float noise such as `0.38999998569488525`), then 8 new columns:

| Column | Type | Rule | Used for |
|---|---|---|---|
| `latitude` … `type` | as CSV | 15 VIIRS columns, same order and names | rows, CSV download |
| `acq_time` | text | always 4 characters (`"0401"`), never a number | rows |
| `utc` | timestamp | `acq_date` + `acq_time` | internal |
| `lst_hour` | float | local solar time in hours = UTC + longitude / 15, from hour and minute | pass |
| `fire_date` | date | date of (local solar time − 6 h) | every date filter in the API |
| `pass` | `D` / `N` | `D` if 6 ≤ `lst_hour` < 18, else `N` | day/night split |
| `origin` | text | `real` (version `2`) · `ml` (`ML-XGB-1.0`) · `gapfill` (`ML-XGB-1.0-gapfill`) | provenance in every response |
| `weight` | double | `1.0` for real rows; for model rows, the table in §5 by month of `fire_date` and `pass` | every count is `SUM(weight)` |
| `ci`, `cj` | int | 0.1° cell index: `floor((lat + 60) / 0.1)`, `floor((lon − 90) / 0.1)` | joins to `daily_cells` |

`fire_date`, `pass`, `ci` and `cj` follow `ml_pipeline/common.py` and `config.py` exactly. **Don't change these rules**, or the API's days and cells will no longer match the model's.

---

## 4. Step 2 — build the summary tables

```bash
python data_build/build_aggregates.py
```

Takes a minute or two. It prints the number of `full`, `partial` and `none` days, and lists the no-data periods. Expect the two MODIS outages: **2001-06-17 → 2001-07-02** and **2002-03-21 → 2002-03-27**.

### `daily_cells.parquet` — one line per cell × fire_date × pass × type × origin

| Column | Meaning |
|---|---|
| `fire_date`, `pass`, `type`, `origin`, `ci`, `cj` | grouping keys |
| `cell_lat`, `cell_lon` | **south-west corner** of the 0.1° cell (what `/api/grid` returns) |
| `count` | `SUM(weight)`: the weighted detection count |
| `frp_sum` | `SUM(frp × weight)`, MW |
| `n_rows` | raw row count |

The API answers `/api/daily`, `/api/calendar`, `/api/anomaly` and `/api/grid` from this table, so even a 26-year calendar for all of Australia stays fast.

### `day_status.parquet` — one line per fire-day, 2000-11-01 → 2025-12-31

| Column | Meaning |
|---|---|
| `fire_date` | every day of the record, including days with no rows |
| `n_rows`, `count` | Australia-wide raw and weighted counts, all types |
| `count_real`, `count_ml`, `count_gapfill` | weighted count per origin |
| `has_data` | `none` = no rows at all · `partial` = fewer than 100 rows Australia-wide, or the day before/after a `none` day · `full` = everything else |
| `daynight_reliable` | `false` on days with gap-fill rows from 2022 onwards (MODIS orbit drift) |

This is computed **for all of Australia, not per box**: whether satellite data exists on a day doesn't depend on the area drawn.

### `weights.json`

The weight table, the uncertainty table, the record dates and the no-data periods found. The API returns these from `/api/meta`.

---

## 5. Weights (why model rows count as more than 1)

Model rows for 2000–2011 and outage days only include VIIRS fires near a MODIS detection, about 89% of all VIIRS detections. Each model row therefore gets a weight that restores the missing share, depending on month and pass:

| Pass | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Day | 1.103 | 1.161 | 1.179 | 1.114 | 1.066 | 1.059 | 1.056 | 1.065 | 1.051 | 1.037 | 1.060 | 1.082 |
| Night | 1.144 | 1.244 | 1.498 | 1.518 | 1.479 | 1.621 | 1.644 | 1.368 | 1.196 | 1.101 | 1.111 | 1.118 |

These come from `harmonic/ml_pipeline/output/models/training_report.json → unreachable_adjust` and are set in `data_build/build_common.py` (`WEIGHTS`). **If the model is retrained, copy the new values from the training report into `WEIGHTS` and rerun steps 1–3.** Check 4 below catches a mismatch.

---

## 6. Step 3 — verify

```bash
python data_build/verify_build.py
```

All checks must print `PASS`, and the script exits with code 0. `--skip-csv-count` skips check 1, which re-reads the whole CSV (a few minutes), for quick reruns.

| # | Check | If it fails |
|---|---|---|
| 1 | Row count per UTC year equals the CSV | Rows were lost or duplicated: rerun step 1 |
| 2 | Every row sits in the folder of its `fire_date` year | Bug in the split |
| 3 | No missing `fire_date`, `utc` or `pass`; `acq_time` always 4 characters | Bad `acq_date` or `acq_time` values in the CSV: look at those rows |
| 4 | Real rows weight 1.0; model rows 1.0–1.7; real rows all have version `2` | An unexpected `version` value: the script prints every version and the origin it was given |
| 5 | `pass` agrees with the CSV's `daynight` in ≥ 97% of rows (expect about 99%) | The local-time rule is wrong |
| 6 | **Weighted total per model year within 1% of `unified_daily_gapfilled.csv`** | The weights don't match the trained model: update `WEIGHTS` (§5) |
| 7 | `daily_cells` totals equal the rows | Rerun step 2 |
| 8 | `day_status` has one line per day, 2000-11-01 → 2025-12-31 | Rerun step 2 |
| 9 | The two MODIS outages are `none` | Unexpected rows in the outage periods |
| 10 | A 2019 one-month box query takes under 1 s | Files not sorted, or the disk is very slow |

For check 6 the script also prints the ratio for every year. Expect about **1.000 for 2000–2011** and for gap-fill years.

---

## 7. Handover to the backend developer

When all checks pass, hand over:

```
pipeline/data_build/data/
├── parquet/
│   ├── rows/year=2000 … year=2025/part-0.parquet
│   ├── daily_cells.parquet
│   └── day_status.parquet
└── weights.json
```

Also send the console output of `verify_build.py`.

For the AGNI website (free tier), upload the `rows/year=*/part-0.parquet` files to the R2 bucket under
`real/source/` and run the **Build dataset** workflow (see `docs/DEPLOYMENT.md`).

- `data/` is git-ignored; don't commit it. For deployment, copy the folder to the server (about 1 GB). The 3 GB CSV doesn't need to be deployed.
- The backend reads the rows with DuckDB: `read_parquet('data/parquet/rows/year=*/*.parquet', hive_partitioning = true)`. Filter on `fire_date`, never on `acq_date`, and sum `weight`, never `count(*)`.

## 8. Don'ts

- Don't edit the CSV or anything in `ml_pipeline`.
- Don't read `acq_time`, `version` or `confidence` as numbers (`0401` becomes `401`; `2` and `ML-…` share one column).
- Don't change the `fire_date`, `pass` or grid rules without changing `ml_pipeline/common.py` and `config.py` too.
- Don't build from the plain `unified_viirs_format.csv`: outage days would look like zero fires.

## 9. Troubleshooting

| Problem | Fix |
|---|---|
| `CSV not found` | Run step 0, or pass `--csv <path>` |
| Out of memory, or very slow and swapping | Close browsers; run with `--memory 2GB` |
| `No space left on device` | DuckDB spills to `data/_tmp` while sorting: free about 6 GB, or pass `--out` on a larger drive |
| Check 6 fails for every model year by the same few % | `WEIGHTS` is from a different training run: update it from `training_report.json` |
| Check 9 fails | Look at the printed no-data periods: new outages may be real, or the CSV may be incomplete |
