# AGNI deployment (free tier)

Everything runs on free plans: GitHub Pages, GitHub Actions, Cloudflare R2 and a Cloudflare Worker.
There is no always-on server. How the parts fit together: [ARCHITECTURE.md](ARCHITECTURE.md).

```
GitHub Pages (website)
  │  the browser reads the dataset files with hyparquet (21 KB): small files whole,
  │  large ones only the row groups a query needs, in parallel
  ▼
Cloudflare Worker  ── serves files from R2, and starts processing for days not stored yet
  ▼
R2 bucket "agni-data"
  mock/ or real/
    manifest.json             what exists, and where each day after the history came from
    hist/cells.parquet, cells_month.parquet, day_status.parquet, day_totals.parquet
                              built from the processed data
    hist/rows/YYYY.parquet    every detection, one file per year (CSV downloads)
    live/{cells,rows}/YYYY-MM.parquet   days after the processed data, one file per month
    source/year=YYYY/part-0.parquet     (real only) the processed per-year Parquet files you upload
    source/day_status.parquet (real only, optional) per-day coverage; worked out from the detections if absent
    forecast/latest.json, forecast/lead_{1,3,7}.parquet   (real only) the experimental forecast
    forecast/weather_observed.parquet   (real only) observed weather for the forecast; uploaded once
    pending/                  markers the Worker writes while days are being processed

GitHub Actions
  Build dataset            per-year Parquet -> hist/*          (manual)
  Process new fire-days    every 3 h + on demand from the Worker:
                           VIIRS from FIRMS (S-NPP; NOAA-20/21 while S-NPP is down),
                           otherwise MODIS from FIRMS -> Harmonic.
                           Also revisits stored days: back to S-NPP when it returns (30 days),
                           and to NASA's final data (with its "type") once published (~2-3 months)
  Add processed days       nightly 03:30 UTC: live days on NASA's final data (or older than 150 days)
                           -> hist/ (rewrites only the years that get days; optionally exports a full
                           CSV). Near-real-time days stay live until upgraded.
  Fire forecast            daily 02:40 UTC (experimental): 1, 3 and 7 days ahead -> forecast/*
  Deploy Worker            on push to worker/
  Deploy frontend          on push to frontend/
```

With nothing configured, the website uses the small mock dataset bundled in
`frontend/public/data/mock`, so it works on GitHub Pages straight away. The FastAPI app in
`backend/` is not part of the deployment; the site uses it only if built with `VITE_API_BASE`
(backend development).

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

### 3. Worker settings
The Worker's non-secret settings are in `worker/wrangler.jsonc` (`vars`): `DATASETS`
(`mock,real`), `ALLOWED_ORIGINS` (the site's origin plus local dev ports), `GITHUB_REPO`
(`owner/name`), `GITHUB_WORKFLOW` (`pipeline.yml`) and `GITHUB_REF` (`main`). The R2 binding is
`DATA` → bucket `agni-data`. Change these if you deploy from a fork or another domain.

### 4. Repository secrets and variables
Repository → Settings → Secrets and variables → Actions.

| Secrets | Used by |
|---|---|
| `FIRMS_MAP_KEY` | NASA FIRMS MAP key. Process new fire-days |
| `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | from step 1.2. Build dataset, Process new fire-days, Add processed days, Fire forecast |
| `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` | from step 1.3. Deploy Worker |
| `DISPATCH_TOKEN` | from step 2 (names can't start with `GITHUB_`). Deploy Worker uploads it as a Worker secret |

| Variables | |
|---|---|
| `AGNI_DATASET` | dataset for scheduled pipeline and consolidate runs: `mock` now, `real` when the real data is in (default `mock`) |
| `VITE_DATA_URL` | `https://agni-api.<account>.workers.dev/data/mock` (or `/data/real`) |
| `VITE_WORKER_URL` | `https://agni-api.<account>.workers.dev` |
| `R2_BUCKET` | optional, bucket name (default `agni-data`) |
| `AGNI_FAKE_FIRMS` | optional `true`: scheduled pipeline runs use fake FIRMS data (for the mock dataset) |

The secrets live only in GitHub (and `DISPATCH_TOKEN` also as a Worker secret). None of them
reaches the website.

### 5. First run
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
   The CSV is not needed. A `real/source/day_status.parquet` (or `.csv`) is optional: if it is
   there it is used, otherwise day coverage (full / partial / none) is worked out from the detections.
2. Actions → **Build dataset** → `real`.
3. Set `AGNI_DATASET=real` and `VITE_DATA_URL=…/data/real`, remove `AGNI_FAKE_FIRMS`, and redeploy the frontend.
4. Set up the forecast (next section).

### Replacing the real data later
For example with an improved model's output:

1. **Empty `real/source/` first.** The build reads every `.parquet` under it, subfolders included,
   so an old file left there would be mixed in. Don't touch `real/hist/`, `real/live/` or
   `real/manifest.json`, and don't delete the whole `real/` folder.
2. Upload the new files into `real/source/`. The day status file stays optional.
3. Actions → **Build dataset → real**. It rebuilds `hist/` and `manifest.json` completely and
   removes old year files. Live days after the new record end are kept.

The record then ends at the last day of the new files. Missing days after it are fetched by the
next pipeline runs (up to 62 per run). Step-by-step guide, including the model swap and the
checks afterwards: [`pipeline/MODEL_AND_DATA_UPDATE.md`](../pipeline/MODEL_AND_DATA_UPDATE.md).

## Forecast (experimental)
The forecast runs only on the real dataset (the workflow's default is `real`; the mock dataset
has none and the site shows no forecast). One-time setup:

1. **Upload the observed weather.** Take the ML team's `australia_daily_weather_recent.parquet`
   and upload it to the bucket as `real/forecast/weather_observed.parquet` (Cloudflare dashboard →
   R2 → `agni-data` → `real/` → folder `forecast`). Each run adds the newest NASA POWER days and
   keeps the last 731 days; without this file the run stops with an error.
2. **Redeploy the Worker** so it serves `forecast/latest.json` and `forecast/lead_N.parquet`:
   any push that changes `worker/`, or Actions → **Deploy Worker to Cloudflare** → Run workflow.
3. **Run it once:** Actions → **Fire forecast (experimental)** → Run workflow, dataset `real`,
   issue empty (= the newest finished fire-day). After that it runs daily at 02:40 UTC.

It needs only the R2 secrets; NASA POWER and Open-Meteo need no key. Model files and updates:
[`pipeline/forecast/README.md`](../pipeline/forecast/README.md).

## Harmonic
The ML team's model lives in `pipeline/harmonic/` (code + trained files). On days when no VIIRS
satellite has data, the pipeline fetches MODIS from 30 days before to 15 days after that day and
runs it through Harmonic; rows are tagged `OUT_VERSION + "-gapfill"` (now `ML-XGB-1.0-gapfill`).
Without the trained files the pipeline refuses to process real days. To update the model, see
`pipeline/harmonic/README.md` and [`pipeline/MODEL_AND_DATA_UPDATE.md`](../pipeline/MODEL_AND_DATA_UPDATE.md):
commit the new files in `ml_pipeline/output/models/`, and the next run uses them.

## Local development
```bash
cd pipeline
python -m agni_pipeline.build_hist --store local:../frontend/public/data/mock --synthetic
python -m agni_pipeline.process_days --store local:../frontend/public/data/mock --fake --days 2026-01-01:2026-11-12 --max-days 400
python -m unittest discover -s tests
```
The Worker runs locally with `npx wrangler dev` in `worker/` (local R2, no account needed).
