# AGNI deployment (free tier)

Everything runs on free plans: GitHub Pages, GitHub Actions, Cloudflare R2 and a Cloudflare Worker.
There is no always-on server.

```
GitHub Pages (website)
  │  the browser reads the dataset files with hyparquet (21 KB): small files whole,
  │  large ones only the row groups a query needs, in parallel
  ▼
Cloudflare Worker  ── serves files from R2, and starts processing for days not stored yet
  ▼
R2 bucket "agni-data"
  mock/ or real/
    manifest.json             what exists, and where each day after the CSV came from
    hist/cells.parquet, cells_month.parquet, day_status.parquet   built from the processed data
    hist/rows/YYYY.parquet    every detection, one file per year (CSV downloads)
    live/{cells,rows}/YYYY-MM.parquet   days after the processed data, one file per month
    source/year=YYYY/part-0.parquet     (real only) the processed per-year Parquet files you upload
    source/day_status.parquet (real only, optional) per-day coverage; worked out from the detections if absent

GitHub Actions
  Build dataset            per-year Parquet -> hist/*          (manual)
  Process new fire-days    every 3 h + on demand from the Worker:
                           VIIRS from FIRMS (S-NPP; NOAA-20/21 while S-NPP is down),
                           otherwise MODIS from FIRMS -> Harmonic.
                           Also revisits stored days: back to S-NPP when it returns (30 days),
                           and to NASA's final data (with its "type") once published (~2-3 months)
  Add processed days       nightly: live days on NASA's final data -> hist/ (rewrites only the current
                           year's detection file; optionally exports a full CSV). Near-real-time
                           days stay live until upgraded (or 150 days have passed).
  Deploy Worker            on push to worker/
  Deploy frontend          on push to frontend/
```

With nothing configured, the website uses the small mock dataset bundled in
`frontend/public/data/mock`, so it works on GitHub Pages straight away.

## One-time setup

### 1. Cloudflare (free plan)
1. Create an account, then **R2 → Create bucket** named `agni-data`.
   Cloudflare may ask for a payment method to enable R2; the free tier (10 GB) is not charged.
2. **R2 → Manage API tokens → Create API token**, permission *Object Read & Write* on `agni-data`.
   Note the *Access Key ID*, *Secret Access Key* and your *Account ID*.
3. **My Profile → API Tokens → Create Token** with the *Edit Cloudflare Workers* template.

### 2. GitHub token for on-demand processing
GitHub → Settings → Developer settings → **Fine-grained tokens** → generate a token for
this repository only, with **Actions: Read and write**. The Worker uses it to start the
"Process new fire-days" workflow.

### 3. Repository secrets and variables
Repository → Settings → Secrets and variables → Actions.

| Secrets | |
|---|---|
| `FIRMS_MAP_KEY` | NASA FIRMS MAP key |
| `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | from step 1.2 |
| `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` | from step 1.3 |
| `DISPATCH_TOKEN` | from step 2 (names can't start with `GITHUB_`) |

| Variables | |
|---|---|
| `AGNI_DATASET` | `mock` now, `real` when the real CSV is in |
| `VITE_DATA_URL` | `https://agni-api.<account>.workers.dev/data/mock` |
| `VITE_WORKER_URL` | `https://agni-api.<account>.workers.dev` |
| `AGNI_FAKE_FIRMS` | optional `true`: scheduled runs use fake FIRMS data (for the mock dataset) |

### 4. First run
1. Actions → **Deploy Worker to Cloudflare** → Run workflow. Note the `workers.dev` URL it prints.
2. Actions → **Build dataset** → `mock`.
3. Actions → **Process new fire-days** → dataset `mock` (tick *fake* to test without FIRMS).
4. Set the two `VITE_*` variables, then Actions → **Deploy frontend to GitHub Pages**.

## Switching to the real data
1. Upload the per-year Parquet files to the bucket under `real/source/`, keeping their year
   folders (`real/source/year=2000/part-0.parquet` … `year=2025/part-0.parquet`; any names work
   as long as they are unique). They are built by `pipeline/data_build/` (see `DATA_BUILD_GUIDE.md`).
   With the AWS CLI (R2 access key from step 1.2, region `auto`), from the folder holding `year=*`:
   `aws s3 cp . s3://agni-data/real/source/ --recursive --exclude "*" --include "year=*/*.parquet" --endpoint-url https://<R2_ACCOUNT_ID>.r2.cloudflarestorage.com`
   The CSV is not needed. Day coverage (full / partial / none) is worked out from the detections;
   `real/source/day_status.parquet` from `data_build` is used instead if you upload it.
2. Actions → **Build dataset** → `real`.
3. Set `AGNI_DATASET=real` and `VITE_DATA_URL=…/data/real`, remove `AGNI_FAKE_FIRMS`, and redeploy the frontend.

To replace the data later (for example with an improved model's output), upload the new files over
the same paths and run **Build dataset → real** again. The record then ends at the last day of those
files again; days after it that were already added are fetched again by the next pipeline runs.

## Harmonic
The ML team's model lives in `pipeline/harmonic/` (code + trained files). On days when VIIRS has
no data, the pipeline fetches MODIS for that day ± 3 days and runs it through Harmonic; rows are
tagged `ML-XGB-1.0-gapfill`. To update the model, see `pipeline/harmonic/README.md`: retrain,
commit the files in `ml_pipeline/output/models/`, and the next run uses them.

## Local development
```bash
cd pipeline
python -m agni_pipeline.build_hist --store local:../frontend/public/data/mock --synthetic
python -m agni_pipeline.process_days --store local:../frontend/public/data/mock --fake --days 2026-01-01:2026-11-12 --max-days 400
python -m unittest discover -s tests
```
The Worker runs locally with `npx wrangler dev` in `worker/` (local R2, no account needed).
