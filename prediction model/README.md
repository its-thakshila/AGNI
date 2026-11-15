# Fire forecast model: VIIRS-format fire rows, 1 / 3 / 7 days ahead

This is the implementation of [Fire_Row_Prediction_Design.pdf](Fire_Row_Prediction_Design.pdf). For an issue day *t*, with every fire up to fire-day *t* known, it forecasts each 0.1° land cell × pass (day/night) on *t + 1*, *t + 3* and *t + 7*:

- the **probability** of at least one real S-NPP VIIRS fire row,
- the **number of rows** if there is fire,
- the row **attributes**: FRP, bright_ti4, bright_ti5 and the l/n/h confidence shares.

It then **synthesizes rows in the VIIRS CSV format** from that grid: the same 15 columns, with `version = FCST-XGB-1.0`.

This is phase 1 of the PDF roadmap, a model that uses fire history only. Weather (phase 2) needs only a switch once the NASA POWER files are in `data/weather/` (see below).

## Files

| File | What it does |
|---|---|
| `fcst_config.py` | All settings: inputs, leads, time splits, sampling rates, XGBoost stages, output version tag |
| `history.py` | Loads the unified + gap-filled fire record and walks the days in order with rolling state, building **leak-free features**: every feature uses fire-days ≤ *t* only |
| `attributes.py` | Stage-3 targets: per cell × fire-day × pass FRP / TI4 / TI5 means and confidence shares from the real VIIRS CSVs (DuckDB, 4 s) |
| `build_dataset.py` | Training tables: one folder per lead, one Parquet file per issue year |
| `fmodels.py` | XGBoost fitting (GPU, CPU fallback), the saved model class, bundle prediction, persistence / climatology baselines |
| `train_forecast.py` | Trains the hurdle models per lead and archives earlier models |
| `evaluate.py` | Scores the test years on the full land grid against both baselines |
| `forecast.py` | Writes the forecast grid and the VIIRS-format rows for an issue day |
| `run_pipeline.py` | Runs all of the above in order |

## Inputs (nothing new to download)

| Input | Used for |
|---|---|
| `backend/data/parquet/daily_cells.parquet` | Fire history (all origins: real, gapfill, V8 model rows 2000–2011) and targets (real rows only) |
| `backend/data/parquet/day_status.parquet` | Skips target days with gap-filled, partial or unreliable day/night data |
| `data/train/VIIRS/viirs-snpp_<YEAR>_Australia.csv` | Stage-3 attribute targets; recent pixel positions and the static layer for the forecast rows |
| `ml_pipeline/output/models/synthesis_stats.joblib` | Overpass-time, pixel-size and within-cell spread statistics, reused from V8 |
| `data/weather/` (optional) | NASA POWER daily weather via `ml_pipeline/weather.py` |

## How to run

```bash
cd "Fire Prediction model"
python run_pipeline.py                   # attributes -> tables -> train -> evaluate -> forecast
python run_pipeline.py --from train      # reuse the tables
python forecast.py                       # forecast from the last day of the history (2025-12-31)
python forecast.py --issue 2024-10-15    # back-test any day: also reports the real rows
```

Each step on this machine (RTX 3050 4 GB, 8 GB RAM):

| Step | Time |
|---|---|
| Tables | about 10 min |
| Training | about 15 min per lead |
| Evaluation | about 15 min |
| One forecast | under a minute |

Earlier models are **moved** to `models_archive/models_<time>[_name]` and never overwritten. Use `--name` to label a run.

## How it works

1. **Unit:** 0.1° cell × fire-day × pass. These are the same keys as the existing pipeline: fire_date is local solar time − 6 h, and passes are D/N.
2. **Features at issue day *t*** (`history.py`, 32 features):

   | Group | Features |
   |---|---|
   | Fire history | Rows on *t* in the same and the other pass, *t−1*, *t−2*; FRP on *t* and over 7 days; 3×3 / 5×5 / 9×9 sums on *t*; 3×3 on *t−1*; 7 and 30-day sums for the cell, 3×3, 5×5 and 9×9 |
   | Fire dynamics | Days since the last fire, burst age, rows 3–12 months earlier (burnt fuel), fire to the N/S/E/W (spread direction), national total on *t* and over 7 days |
   | Climatology | Per cell × target month × pass: mean rows and share of fire days, from 2001–2021 |
   | Calendar and place | Pass, day of year (sin/cos), weekday of the target day, lat/lon |

3. **Three stages per lead** (separate models for 1, 3 and 7 days):
   - **Stage 1, occurrence:** XGBoost `binary:logistic`, P(any real row).
   - **Stage 2, rows if there is fire:** XGBoost `count:poisson`, trained on cells with fire.
   - **Stage 3, attributes:** log FRP, TI4 and TI5 regressors, plus an l/n/h `softprob` classifier.
   - **Expected rows** = p × rows-if-fire × a month × pass calibration factor fitted on the validation year.
4. **Training rows:** every land cell × pass is a candidate. Every row with fire is kept. No-fire rows are kept at 2% in the active region (fire in the 5×5 within 30 days) and at 0.4% elsewhere, weighted 1/rate so totals stay unbiased. That gives about 9.3 M rows per lead, 1.9 M of them with fire.
5. **Rows** (`forecast.py`):
   - Expected rows are rounded stochastically, which keeps the totals.
   - Each row is placed on a recent VIIRS pixel of the same cell (fire-days *t−6..t*) with 375 m jitter; cells without one get a random position inside the cell.
   - Overpass time comes from the S-NPP local-time distribution for the pass.
   - FRP, TI4, TI5 and confidence come from the stage-3 means plus the within-cell spread; the brightest pixels get "h".
   - Static sources (type 2/3) are not forecast. Their mean daily rate over the last year is added as a fixed layer.

### Protections against leakage

- **Time split by target year:**
  - train 2012–2021,
  - validate on 2022 (early stopping and calibration),
  - test 2023–2025 (scored only by `evaluate.py`).
- **Only real VIIRS rows are targets.** The V8 rows from 2000–2011 are only ever history inputs.
- **Features come from fire-days ≤ *t*.** None of the V8/V9 features that look at next-day MODIS are used.
- **Leave-one-year-out climatology** for target days inside 2001–2021. Otherwise a training row's own fire count is part of its climatology feature. This was found and fixed during development: with the leak, the count model overfit after about 30 rounds; without it, it keeps improving to 2000 rounds.

## Outputs

`output/forecasts/` (`<t>` = issue date):

**`forecast_cells_<t>_lead<L>.csv`** is the forecast itself: one row per cell × pass with P ≥ 0.005 or expected rows ≥ 0.01.

| Column | Meaning |
|---|---|
| `target_date`, `pass` | Day and pass forecast |
| `cell_lat`, `cell_lon` | Cell centre |
| `p_fire` | Probability of at least one real VIIRS row |
| `rows_if_fire` | Expected rows if there is fire |
| `expected_rows` | Expected rows overall |
| `frp_mean`, `ti4`, `ti5` | Mean row attributes |
| `p_conf_l/n/h` | Confidence shares |

**`forecast_rows_<t>_lead<L>.csv`** holds the rows in VIIRS format, with the same 15 columns as `viirs-snpp_<YEAR>_Australia.csv`:

- `version = FCST-XGB-1.0`, so forecast rows can't be mixed up with real (`2`) or harmonised (`ML-XGB-1.0`) rows.
- `type 0` rows are the forecast fires; `type 2/3` rows are the static layer.
- These rows are **one plausible sample**, not exact predicted pixels. The cell grid is the forecast.

**`forecast_summary_<t>.json`** gives, per lead:

- the expected national total,
- a P5–P95 spread, which treats cells as independent and so understates the real spread,
- in a back-test, the real rows and the share of them inside the top-1000 cells.

`output/evaluation/` holds:

- `evaluation_summary.md` and `evaluation_report.json`: test-year scores against persistence and climatology,
- `daily_totals.csv` and `daily_totals.png`.

`models/` holds `lead_<L>.joblib` bundles and `training_report.json`: validation scores, round counts, feature importance and baseline rates.

## Results (test years 2023–2025)

This is the first trained run, `v1_history_only`, evaluated 15 Nov 2026. Scoring used every 2nd issue day of 2023–2025 (534 target days per lead) on the full land grid of 60,461 cells × 2 passes. None of these years was used in training or tuning. The full table is in `output/evaluation/evaluation_summary.md`.

| Lead | Method | PR-AUC | FSS 0.1° | FSS 0.5° | Top-1000 row capture | Poisson dev/cell | Daily total log corr | Daily total bias |
|---|---|---|---|---|---|---|---|---|
| 1 d | **model** | **0.425** | **0.470** | **0.753** | **64%** | **0.154** | **0.936** | −4.7% |
| | persistence | 0.315 | 0.287 | 0.500 | 56% | 0.256 | 0.874 | −6.9% |
| | climatology | 0.032 | 0.078 | 0.336 | 11% | 0.371 | 0.667 | −6.7% |
| 3 d | **model** | **0.174** | **0.201** | **0.515** | **36%** | **0.243** | **0.848** | −10.1% |
| | persistence | 0.098 | 0.088 | 0.226 | 27% | 0.350 | 0.730 | −9.7% |
| 7 d | **model** | **0.082** | **0.097** | **0.369** | **21%** | **0.291** | **0.795** | −15.9% |
| | persistence | 0.029 | 0.028 | 0.113 | 12% | 0.383 | 0.625 | −11.4% |
| | climatology | 0.032 | 0.078 | 0.336 | 11% | 0.371 | 0.667 | −6.7% |

**The model beats both baselines on every skill measure at every lead.** As the PDF predicted, skill falls quickly with lead time: at 7 days the model has about 2.5× the PR-AUC of either baseline, but cell-level skill is modest.

**Known weaknesses of v1:**

- **National totals run low at longer leads,** mostly in the record spring 2023 season:

  | Year | Real rows | Model, lead 1 | Model, lead 7 |
  |---|---|---|---|
  | 2023 | 0.93 M | 0.84 M | 0.71 M |
  | 2024 | 0.48 M | 0.47 M | 0.44 M |
  | 2025 | 0.62 M | 0.62 M | 0.56 M |

  The calibration year, 2022, was quiet, which keeps forecasts low. Weather (phase 2) is the expected fix for season-level swings.
- **Probabilities are reliable up to about 0.3, then somewhat overconfident** (lead 1: 0.85 predicted vs 0.76 observed in the top bin).
- **Row attributes:** the forecast cell-mean FRP runs about 25% high (log bias +0.22). Synthesized rows also have a thinner extreme-FRP tail than real VIIRS (back-test 2024-10-16: p99 46 vs 113 MW).

**First forward forecast** (issue 2025-12-31, the last day of the record), in `output/forecasts/`:

| Target day | Expected fire rows | P5–P95 |
|---|---|---|
| 2026-01-01 | 419 | 318–481 |
| 2026-01-03 | 523 | 415–624 |
| 2026-01-07 | 740 | 615–842 |

## Later runs: weather and unified targets (15 Nov 2026)

| Run | Targets | Weather | Train / val / test | Folder |
|---|---|---|---|---|
| v1 | real VIIRS | none | 2012–21 / 2022 / 2023–25 | `models_archive/..._v1_history_only` |
| v2 | real VIIRS | issue day *t* (usable live without a weather forecast) | same | `models_archive/..._v2_weather_issue` (stopped before evaluation) |
| v3 | real VIIRS | target day (observed in tests = perfect weather forecast) | same | `models_archive/..._v3_weather_target` |
| v4 | unified gap-filled CSV (Harmonic rows before 2012) | target day | 2003–23 / 2024 / 2025 | `models/` (active) |

- **v2:** issue-day weather left validation PR-AUC unchanged (0.393 / 0.159 / 0.078 vs 0.392 / 0.158 / 0.079).
- **v3 vs v1** on 2023–25, PR-AUC: 0.439 vs 0.425 at 1 d, 0.200 vs 0.174 at 3 d, 0.102 vs 0.082 at 7 d. The daily-total bias fell from −4.7 / −10.1 / −15.9% to −1.0 / −0.6 / −9.0%. With real forecast weather the gain will be smaller, especially at 7 d.
- **v4 vs v3** on 2025, same days and targets:

  | Lead | Model | PR-AUC | Poisson dev/cell | FSS 0.5° | FSS 1.1° | Top-1000 capture | Total bias |
  |---|---|---|---|---|---|---|---|
  | 1 d | v3 | 0.409 | 0.139 | 0.745 | 0.832 | 64.9% | +6.4% |
  | 1 d | v4 | 0.401 | 0.142 | 0.747 | 0.830 | 64.6% | −1.3% |
  | 3 d | v3 | 0.178 | 0.211 | 0.566 | 0.719 | 39.0% | +9.4% |
  | 3 d | v4 | 0.175 | 0.213 | 0.576 | 0.725 | 38.5% | −7.0% |
  | 7 d | v3 | 0.090 | 0.247 | 0.431 | 0.631 | 24.1% | +2.0% |
  | 7 d | v4 | 0.089 | 0.249 | 0.460 | 0.655 | 23.8% | −14.1% |

  Adding 2003–2011 as targets gives no clear gain: cell-level scores are slightly lower, regional maps at 7 d are slightly better. Total bias mostly reflects the calibration year (2022 for v3, 2024 for v4).

**Switches:** `TARGET_SOURCE` in `fcst_config.py` (`"unified"` or `"real"`) and `run_pipeline.py --weather [issue|target]`.

**Forecasting with a weather forecast** (target-day models only):

```bash
python forecast.py --issue 2025-12-31 --weather-forecast my_forecast.csv
python forecast.py --issue 2025-12-31 --target 2026-01-07 --weather-forecast my_forecast.csv
```

- The file uses the POWER columns (`weather_forecast_template.csv`): date, latitude, longitude, T2M_MAX, T2M_MIN, PRECTOTCORR, RH2M, WS10M, and optionally PS.
- It must hold every day from *t*+1 to the target day. Any grid works: each POWER point uses the nearest forecast point within 1°.
- `forecast_weather.py` joins it to the observed weather up to *t* for the rain sums and dry-day counts. Fed observed weather, it reproduces the training features exactly; only the temperature anomaly differs, by up to 0.04 °C.

## Adding weather (phase 2)

1. Put the NASA POWER daily files in `data/weather/`. They need to cover 2011-12 → 2025-12 for training and testing (see `ml_pipeline/V9_WEATHER_MODEL.md`).
2. Run `python run_pipeline.py --weather --name v2_weather`. This adds the 12 V9 weather features for the target day.
3. Compare its `evaluation_summary.md` with the history-only run, which is kept in `models_archive/`.

> **Caution:** POWER is *observed* weather. Testing with it is a "perfect weather forecast" and overstates the skill at 3 and 7 days. Live forecasts need a weather-forecast source (section 4 of the PDF).

## Limits

- **New ignitions** with no fire nearby in the previous week (about 12% of rows) can only get low, spread-out probabilities.
- **Live forecasting** past 2025-12-31 needs new fire data. Append FIRMS near-real-time VIIRS to the fire record (`daily_cells.parquet` or the unified CSV), then issue from the newest day.
- **Not built (optional phases):** the ConvLSTM / U-Net challenger (phase 4), the live feeds and a backend layer (phase 5).
- **`--final`** refits on all built years with the evaluated round counts, after running `build_dataset.py --with-test`. Use it for live forecasts; its own scores would be in-sample.
