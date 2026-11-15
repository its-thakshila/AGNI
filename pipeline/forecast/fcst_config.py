"""Settings for the fire forecast model (Fire_Row_Prediction_Design.pdf).

Forecast = for issue day t (all fires of fire-day t are known), predict every 0.1 deg land cell x pass
(D/N) on target day t + lead: P(any VIIRS row), rows if there is fire, and the row attributes.
Rows in the VIIRS CSV format are then synthesized from that grid.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent              # pipeline/forecast/ in the AGNI repo
ROOT = HERE.parent                                  # pipeline/
# Harmonic's ml_pipeline (grid, VIIRS schema, weather.py). In the ML team's own folder layout it sits
# next to this folder; in the AGNI repo it is pipeline/harmonic/ml_pipeline.
ML = ROOT / "harmonic" / "ml_pipeline" if (ROOT / "harmonic" / "ml_pipeline").exists() else ROOT / "ml_pipeline"
if str(ML) not in sys.path:                         # reuse grid, VIIRS schema, synthesis stats, weather.py
    sys.path.insert(0, str(ML))

import config as mlc                                # noqa: E402  (ml_pipeline/config.py)

# ------------------------------------------------------------ inputs
CELLS_PARQUET = ROOT / "backend" / "data" / "parquet" / "daily_cells.parquet"   # unified + gap-filled record
DAY_STATUS_PARQUET = ROOT / "backend" / "data" / "parquet" / "day_status.parquet"
VIIRS_DIR = mlc.TRAIN_VIIRS_DIR                     # real VIIRS CSVs: stage-3 targets + row positions
UNIFIED_ROWS_CSV = mlc.UNIFIED_DIR / "unified_viirs_format_gapfilled.csv"   # positions when no VIIRS CSV
SYNTH_STATS_FILE = mlc.SYNTH_STATS_FILE             # pixel-size / overpass / spread stats from V8

# ------------------------------------------------------------ outputs
OUT = HERE / "output"
CACHE = OUT / "cache"
ATTR_PARQUET = CACHE / "viirs_cell_attributes.parquet"          # stage-3 targets from the real VIIRS CSVs
ATTR_PARQUET_UNIFIED = CACHE / "unified_cell_attributes.parquet"  # ... from the unified gap-filled CSV
TABLE_DIR = OUT / "tables"                          # training tables, one folder per lead
MODEL_DIR = HERE / "models"
ARCHIVE_DIR = HERE / "models_archive"               # earlier model folders are moved here, never deleted
EVAL_DIR = OUT / "evaluation"
FORECAST_DIR = OUT / "forecasts"

# ------------------------------------------------------------ task
LEADS = (1, 3, 7)                                   # days ahead; one model per lead ("direct" strategy)
REAL_START = "2012-01-20"                           # first real S-NPP VIIRS fire-day
# What the model learns to forecast (the targets):
#   "real"    real S-NPP VIIRS rows only, complete days without gap-fill (runs v1-v3: 2012-2021 / 2022 / 2023-25)
#   "unified" every row of unified_viirs_format_gapfilled.csv: real VIIRS from 2012, Harmonic (V8) rows
#             converted from MODIS before 2012, gap-filled days. Lets training start in 2003, but before 2012
#             the targets are model estimates, not observations.
TARGET_SOURCE = os.environ.get("FCST_TARGETS", "unified")   # env FCST_TARGETS=real|unified overrides
TARGET_START = "2003-01-01" if TARGET_SOURCE == "unified" else REAL_START
# Split by TARGET day year. Never random: neighbouring cells / days are near copies.
# Since v5 (both target sources): test = 2025; every other year is fitted, and early stopping and calibration
# use year-block cross-validation (CV_BLOCKS): models trained on the other blocks predict each block, so the
# calibration sees honest predictions for every year instead of one validation year.
# (v1-v3: train 2012-2021 / val 2022 / test 2023-25; v4: train 2003-2023 / val 2024 / test 2025.)
if TARGET_SOURCE == "unified":
    TRAIN_YEARS = range(2003, 2024)                 # 2003-2023  ("train" in the tables)
    VAL_YEARS = (2024,)                             # ("val" in the tables; fitted too when CV_BLOCKS is set)
    CV_BLOCKS = [(2003, 2009), (2010, 2016), (2017, 2024)]
else:
    TRAIN_YEARS = range(2012, 2024)                 # 2012-2023
    VAL_YEARS = (2024,)
    CV_BLOCKS = [(2012, 2015), (2016, 2019), (2020, 2024)]
TEST_YEARS = (2025,)                                # scored once by evaluate.py
CLIM_YEARS = range(2001, 2025)                      # leave-one-year-out inside; 2025 (test) never used
FIRE_TYPES = (0,)                                   # vegetation fires; type 2/3 = static layer (forecast.py)

# ------------------------------------------------------------ training rows
# Every land cell x pass is a candidate. Rows with fire on the target day are always kept; the rest
# are kept with these rates and weighted 1/rate, so weighted sums stay unbiased (like EMPTY_KEEP).
# Halved for "unified" (21 training years instead of 10) so the tables still fit in 8 GB RAM.
ACTIVE_KEEP = 0.01 if TARGET_SOURCE == "unified" else 0.02   # no-fire rows in the "active region": fire in
                                                              # the 5x5 within the last 30 days
QUIET_KEEP = 0.002 if TARGET_SOURCE == "unified" else 0.004  # no-fire rows elsewhere
SEED = 42

# ------------------------------------------------------------ models (XGBoost)
XGB_DEVICE = mlc.XGB_DEVICE
XGB_COMMON = dict(tree_method="hist", grow_policy="lossguide", max_depth=0, max_bin=256,
                  learning_rate=0.06, reg_lambda=1.0, seed=SEED, verbosity=1)
# Stage 1 stops on validation PR-AUC, not log-loss: with sampling weights up to 1/QUIET_KEEP = 250 the
# weighted log-loss is dominated by a few quiet rows and stopped after ~40 rounds while PR-AUC still
# improved (tested Nov 2026: 127 leaves + min_child_weight 100 + subsampling + max_delta_step 1 gave
# val PR-AUC 0.35 vs 0.32 for the 63-leaf default).
STAGES = {
    "occ":  dict(objective="binary:logistic", eval_metric="aucpr", max_leaves=127, min_child_weight=100.0,
                 learning_rate=0.04, subsample=0.8, colsample_bynode=0.8, max_delta_step=1.0,
                 rounds=1500, stop=100),
    "cnt":  dict(objective="count:poisson", eval_metric="poisson-nloglik", max_leaves=31, min_child_weight=20.0,
                 learning_rate=0.04, subsample=0.8, colsample_bynode=0.8, reg_lambda=10.0, max_delta_step=0.7,
                 rounds=2000, stop=50),
    "reg":  dict(objective="reg:squarederror", eval_metric="rmse", max_leaves=31, min_child_weight=40.0,
                 rounds=600, stop=30),
    "conf": dict(objective="multi:softprob", eval_metric="mlogloss", num_class=3, max_leaves=31,
                 min_child_weight=5.0, rounds=400, stop=30),
}
ATTR_MAX_ROWS = 800_000   # positives used for the stage-3 attribute models (random subset)
CALIB_SHRINK = 500.0      # month x pass calibration factor = (actual + K) / (predicted + K) on validation
CV_ROUNDS_FACTOR = 1.1    # final refit on all fitted years: mean best rounds of the CV folds x this
CALIB_MIN_YEAR = 2012     # CV calibration (probability correction + totals) uses out-of-fold years >= this
ISO_BINS = 4000           # probability correction (isotonic) fitted on this many weighted quantile bins

USE_WEATHER = False       # False | "issue" | "target": add the 12 NASA POWER features of ml_pipeline/weather.py
                          # for the issue day t (known when the forecast is made: usable live) or for the target
                          # day (observed weather = a perfect weather forecast: an upper bound only).
                          # Needs the POWER files in data/weather; run_pipeline.py --weather [issue|target].

# ------------------------------------------------------------ output rows
VIIRS_COLUMNS = mlc.VIIRS_COLUMNS
OUT_VERSION = "FCST-XGB-1.0"
STATIC_DAYS = 365         # type-2 static sources: mean daily rate over this many days before the issue day
EVAL_EVERY = 2            # evaluate.py scores every 2nd test issue day on the full land grid
TOPK = 1000               # evaluate.py: share of real rows inside the top-k cell-passes of each day
