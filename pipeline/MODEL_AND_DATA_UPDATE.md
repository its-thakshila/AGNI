# Updating Harmonic and the real dataset

This guide replaces the Harmonic model with a new one and replaces the website's real data
with the Parquet files the new model produced. There are two independent parts:

| Part | Where | What it affects |
|---|---|---|
| **A. Model** | this repo, `pipeline/harmonic/ml_pipeline/` | days estimated from now on (days when VIIRS has no data) |
| **B. Data** | the R2 bucket `agni-data`, folder `real/source/` | the whole history shown on the website |

Do part A first, then part B, then part C (checks).

---

## How the data is organised in R2 (read this first)

Everything for the real website lives under `real/` in the bucket:

```
real/
  source/          ← INPUT: the per-year Parquet files you upload. Only you write here.
  hist/            ← OUTPUT: built by the "Build dataset" action from source/. Never edit by hand.
  live/            ← OUTPUT: days added after the source files end (fetched from NASA every 3 hours)
  manifest.json    ← OUTPUT: index the website reads (date range, which days exist, …)
```

- **Only replace `real/source/`.** The "Build dataset" action rebuilds `hist/` and
  `manifest.json` from it completely and removes any old year files in `hist/`, so nothing
  from the old data survives there.
- **Don't delete the whole `real/` folder.** The website would show an error until the build
  finishes, and every day after your files end (for example August 2026 to today) would have to
  be fetched from NASA again. Replacing `source/` is enough to remove all old data.
- **Day status file:** the build can take an optional `day_status` file saying which days have
  data. We have never uploaded one and you don't need one: the build works it out from the
  detections (days with no rows = satellite outage, few rows = partial day). Ignore it.

---

## A. Replace the model (in the repo)

### A1. Copy the new files

From the new model, copy into `pipeline/harmonic/ml_pipeline/`:

- **Always** the three trained files in `output/models/`:
  - `hgb_modis_to_viirs.joblib`
  - `synthesis_stats.joblib`
  - `training_report.json`
- **If the code changed too**, the prediction code: `config.py`, `common.py`, `features.py`,
  `gbm.py`, `predict.py` (the training and report scripts can be replaced as well).

The pipeline calls only these three functions from `predict.py`, so they must keep the same
names and arguments: `load_models()`, `predict_cells(m, bundles)`, `synthesize_rows(X, m, stats, rng)`.

Don't change `pipeline/agni_pipeline/harmonic.py`. It's a small adapter that feeds NASA's MODIS
rows to `predict.py`; it works with any model that keeps those three functions.

### A2. Give the new model a version name

In `pipeline/harmonic/ml_pipeline/config.py`, set `OUT_VERSION` to the new model's name, e.g.

```python
OUT_VERSION = "ML-XGB-2.0"
```

It must start with `ML-`. Rows the model makes are tagged with it (`ML-XGB-2.0` for
pre-VIIRS years, `ML-XGB-2.0-gapfill` for gap days), which is how the website knows a row is an
estimate.

Then replace `ML-XGB-1.0-gapfill` with the new name (e.g. `ML-XGB-2.0-gapfill`) in
`pipeline/tests/test_pipeline.py` (3 places).

### A3. Match the XGBoost version

Open `output/models/training_report.json` and find `"model"`, e.g. `"XGBoost 3.2.0 (GPU, CUDA)"`.
In `pipeline/requirements.txt`, set the same version on the `xgboost-cpu` line:

```
xgboost-cpu==3.2.0
```

### A4. Test and push

From the `pipeline/` folder:

```bash
pip install -r requirements.txt
python -m unittest discover tests
```

All tests must pass. Then commit and push. From the next run, the 3-hourly pipeline uses the
new model for any day it has to estimate.

---

## B. Replace the data (in R2)

### B1. Check the new Parquet files

Each file holds detections in the 15 VIIRS columns, in any order:

```
latitude, longitude, bright_ti4, scan, track, acq_date, acq_time, satellite, instrument,
confidence, version, bright_ti5, frp, daynight, type
```

- `version`: `2` (or `2.0`) for real VIIRS rows, `ML-…` for model rows, ending in `-gapfill`
  for gap-filled days. Any other value stops the build with "Unknown version".
- An extra `weight` column is optional. If present, it is used as is; if not, the build uses
  the standard monthly day/night weights.
- One file per year is usual (e.g. `year=2019/part-0.parquet`), but names and subfolders don't
  matter: the build reads every `.parquet` file under `real/source/`.
- Note the last date in the files (e.g. 2025-12-31 or 2026-07-31). You need it in C2.

### B2. Swap the files in R2

In the Cloudflare dashboard → **R2** → bucket **agni-data** → folder **real/source/**:

1. **Delete everything inside `real/source/`.** This is important: the build reads every
   `.parquet` under that folder, so any old file left there would be mixed in with the new data.
2. Upload the new Parquet files into `real/source/` (subfolders like `year=2019/` are fine).

Don't touch `real/hist/`, `real/live/` or `real/manifest.json`.

While this is in progress the website keeps showing the old data, because it reads `hist/`, not
`source/`.

### B3. Rebuild

GitHub → **Actions** → **Build dataset** → **Run workflow** → dataset **real**.

It takes 10–40 minutes. When it finishes, the website shows the new data (reload the page).

---

## C. After the rebuild

### C1. Check the website

- The calendar shows every year from 2000 and the record ends where your files end, then
  continues with the live days.
- Open a pre-2012 month: the source note should say "Harmonized from MODIS … by Harmonic".

### C2. Days after your files end

- **Days between the end of your files and the start of the live data** (for example, if your
  files end on 2025-12-31, the months Jan–Jul 2026) are fetched again from NASA automatically:
  the 3-hourly run adds up to 62 missing days each time, so it catches up within about a day.
  To speed it up: Actions → **Process new fire-days** → dataset **real**, days empty → run it a
  few times, one after the other.
- **Live days the old model estimated** (days after your files end with no VIIRS data) are not
  redone automatically. To recompute them with the new model, run **Process new fire-days**
  with dataset **real** and these days (in two runs, 62 days is the limit per run):
  - `2026-08-01:2026-09-30`
  - `2026-10-01:2026-11-15` (up to today)

  Days that have VIIRS are re-fetched unchanged, so it's safe to include them.

### C3. Accuracy figures shown on the website

These are written in the code and must be updated by hand from the new `training_report.json`:

| File | What |
|---|---|
| `pipeline/agni_pipeline/build_hist.py` → `PERIODS` | typical daily error of model years: `0.16` (Terra only, 2000–2002) and `0.11` (Terra + Aqua, 2002–2011) |
| `frontend/src/components/AboutDialog.tsx` | "±3% monthly" |
| `frontend/src/lib/colors.ts` → `ml` note | "about ±3–5%" |

`PERIODS` is read during the build, so if it changes, run **Build dataset → real** again after
pushing.

The monthly day/night weights for newly estimated days are read from `training_report.json`
automatically; nothing to change for those.
