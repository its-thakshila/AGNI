# AGNI deployment (free tier)

Everything runs on free plans: GitHub Pages, GitHub Actions, Cloudflare R2 and a Cloudflare Worker.
There is no always-on server.

```
GitHub Pages (website)
  │  DuckDB-WASM in the browser reads the dataset files (only the parts a query needs)
  ▼
Cloudflare Worker  ── serves files from R2, and starts processing for days not stored yet
  ▼
R2 bucket "agni-data"
  mock/ or real/
    manifest.json             what exists, and where each day after the CSV came from
    hist/*.parquet            built once from the processed CSV
    live/{cells,rows}/YYYY-MM.parquet   days after the CSV
    source/*.parquet          (real only) the processed per-year Parquet files you upload
    source/day_status.csv     (real only, optional) per-day coverage: fire_date,has_data,daynight_reliable,flag

GitHub Actions
  Build dataset            per-year Parquet -> hist/*          (manual)
  Process new fire-days    every 3 h + on demand from the Worker:
                           VIIRS S-NPP from FIRMS if it has data for the day,
                           otherwise MODIS from FIRMS -> Harmonic
  Add processed days       nightly: live days -> main dataset (optionally re-export the CSV)
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
1. Upload the per-year Parquet files to the bucket under `real/source/`
   (for example `real/source/viirs_2019.parquet`; the names don't matter, the dashboard upload works).
   They are built from the unified CSV by `pipeline/data_build/` (see `DATA_BUILD_GUIDE.md` there).
   Optionally add `real/source/day_status.csv` so outage days are told apart from quiet days;
   without it, days with detections count as covered and days without as "no data".
2. Actions → **Build dataset** → `real`.
3. Set `AGNI_DATASET=real` and `VITE_DATA_URL=…/data/real`, redeploy the frontend.

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
