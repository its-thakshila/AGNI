# Fire forecast (experimental)

This folder belongs to the ML team. It holds the forecast model's code and trained models, as the
ML pipeline produces them. The model in `models/` is **forecast v5** (`FCST-XGB-1.0`, trained
15 Nov 2026): one XGBoost model per lead time (**1, 3 and 7 days** ahead; no other leads), with
48 features per 0.1° cell:

- **fire history** (32): the cell's own and its neighbours' detections over the last days, weeks
  and year, FRP, days since the last fire, national totals, climatology, season, location;
- **weather of the target day** (16): 12 NASA POWER-style features (temperature, humidity, wind,
  rain sums, dry days, VPD, max-temperature anomaly, …) plus KBDI, drought factor, FFDI and its
  7-day maximum.

Tested on 2025 (`models/evaluation_report.json`, 183 issue days):

| Lead | National daily total error | Repeating the last day's fires | Detections in the top-1000 cells |
|------|---------------------------:|-------------------------------:|---------------------------------:|
| 1 day  | 29% | 59%  | 65% |
| 3 days | 35% | 106% | 39% |
| 7 days | 40% | 123% | 24% |

```
forecast/
  fcst_config.py fmodels.py history.py fire_danger.py   ← used at forecast time
  models/lead_1.joblib lead_3.joblib lead_7.joblib      ← trained models (one per lead)
  models/training_report.json                           ← features, training settings
  models/evaluation_report.json                         ← 2025 test scores (shown on the website)
  models/tmax_normals.parquet                           ← normal max temperature per POWER point and day
```

The modules also import Harmonic's `ml_pipeline` (`fcst_config.py` adds it to the path).

## How it runs

`pipeline/agni_pipeline/forecast.py`, run by the GitHub Action **"Fire forecast (experimental)"**
(`.github/workflows/forecast.yml`) once a day at **02:40 UTC** (12:40 in eastern Australia). A run
takes about 3 minutes. It forecasts from the newest finished fire-day (or the *issue* day given).

| Input | Where from |
|-------|-----------|
| Fire record | R2: `hist/cells.parquet` + `live/cells/*.parquet` (vegetation fires, all origins) |
| Observed weather | R2: `forecast/weather_observed.parquet` — the last 731 days of NASA POWER daily data. Each run adds the new days from the POWER regional API (10×10° boxes, one parameter per request) and refetches the last 10. POWER lags about 3 days; those days are filled from Open-Meteo. |
| Weather forecast | Open-Meteo (free tier): about 700 points (every 2nd POWER point), each POWER point takes the nearest within 1°. Data CC BY 4.0, credited on the website. |
| Temperature normals | `models/tmax_normals.parquet` (from the ML team) |

Outputs, written to R2 under the dataset prefix and served by the Worker:

| File | Contents |
|------|----------|
| `forecast/latest.json` | issue day, target days, national totals with a P5–P95 range, accuracy, weather sources, time made (served `no-store`) |
| `forecast/lead_<L>.parquet` | one row per cell worth showing: `ci`, `cj` (website grid: `floor(lon*10)`, `floor(lat*10)`), `p` = chance of at least one detection in either pass (day or night), `expected` = expected detections |

Run it by hand:

```
cd pipeline
python -m agni_pipeline.forecast --store r2:real
python -m agni_pipeline.forecast --store local:../data/real --issue 2026-11-14
```

## One-time setup

1. **Upload the observed weather.** Take the ML team's `australia_daily_weather_recent.parquet`
   and put it in the R2 bucket `agni-data` as `real/forecast/weather_observed.parquet`:
   Cloudflare dashboard → R2 → `agni-data` → `real/` → create folder `forecast` → upload the file,
   then rename it to `weather_observed.parquet` (or upload it under that name). The forecast run
   keeps it up to date from then on.
2. **Push to `main`.** The Worker must be redeployed so it serves the new forecast files
   (`forecast/latest.json`, `forecast/lead_N.parquet`). The "Deploy Worker to Cloudflare" Action
   (`.github/workflows/deploy-worker.yml`) does this on any push that changes `worker/`; you can
   also start it from Actions → "Deploy Worker to Cloudflare" → Run workflow.
3. **Run the first forecast.** Actions → "Fire forecast (experimental)" → Run workflow, dataset
   `real`, issue empty. After that it runs daily at 02:40 UTC by itself.

## Updating the model

1. Replace `models/lead_1.joblib`, `lead_3.joblib`, `lead_7.joblib`, `training_report.json` and
   `evaluation_report.json` with the new ones (and the `.py` files if they changed).
2. Replace `models/tmax_normals.parquet` only if the weather grid changes.
3. If the model's version changes, set `OUT_VERSION` in `fcst_config.py`.
4. Commit and push. The next daily run uses the new model.

Nothing in `pipeline/agni_pipeline/forecast.py` needs to change, as long as the leads stay 1, 3
and 7 and the modules keep their interfaces: `fmodels.load_bundles()` / `fmodels.predict(b, X,
month)` and `history.Day.features(i, j, night, lead, weather)`, with the same feature names.

## Requirements

Forecasting needs `pandas`, `numpy`, `xgboost`, `joblib` and `duckdb` (in
`pipeline/requirements.txt`). Keep `xgboost-cpu` there at the version the model was trained with
(`training_report.json` → `"device"`, now 3.2.0, the same as Harmonic).
