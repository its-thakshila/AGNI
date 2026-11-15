"""Settings for the daily MODIS -> VIIRS(S-NPP) ML harmonization pipeline (Australia).

All input and output paths live here; the other scripts import them from this module.
"""
from __future__ import annotations

from pathlib import Path

import re

ROOT = Path(__file__).resolve().parents[1]     # project folder (d:\NASA)
HERE = Path(__file__).resolve().parent         # ml_pipeline/

# ------------------------------------------------------------ input data
#   data/train/MODIS    modis_<YEAR>_Australia.csv       years with a VIIRS twin (2012+)
#   data/train/VIIRS    viirs-snpp_<YEAR>_Australia.csv
#   data/predict        modis_<YEAR>_Australia.csv       MODIS-only years (2000-2011)
DATA_DIR = ROOT / "data"
TRAIN_MODIS_DIR = DATA_DIR / "train" / "MODIS"
TRAIN_VIIRS_DIR = DATA_DIR / "train" / "VIIRS"
PREDICT_DIR = DATA_DIR / "predict"
MODIS_DIRS = (TRAIN_MODIS_DIR, PREDICT_DIR)    # searched in this order

MODIS_NAME = "modis_{year}_Australia.csv"
VIIRS_NAME = "viirs-snpp_{year}_Australia.csv"


def modis_path(year: int) -> Path:
    """MODIS CSV for a year, from data/train/MODIS or data/predict."""
    for d in MODIS_DIRS:
        p = d / MODIS_NAME.format(year=year)
        if p.exists():
            return p
    raise FileNotFoundError(f"no {MODIS_NAME.format(year=year)} in " + ", ".join(map(str, MODIS_DIRS)))


def viirs_path(year: int) -> Path | None:
    """Real VIIRS CSV for a year, or None if that year has no VIIRS (pre-2012)."""
    p = TRAIN_VIIRS_DIR / VIIRS_NAME.format(year=year)
    return p if p.exists() else None


def modis_years() -> list[int]:
    """Every year with a MODIS CSV in any data folder."""
    return sorted({int(re.search(r"_(\d{4})_", p.name).group(1))
                   for d in MODIS_DIRS for p in d.glob(MODIS_NAME.format(year="*"))})


# Training pairs = MODIS/VIIRS overlap years (S-NPP VIIRS starts 2012).
TRAIN_YEARS = range(2012, 2026)
TRAIN_PAIRS = [(TRAIN_MODIS_DIR / MODIS_NAME.format(year=y), TRAIN_VIIRS_DIR / VIIRS_NAME.format(year=y))
               for y in TRAIN_YEARS]


def check_train_files():
    missing = [p for pair in TRAIN_PAIRS for p in pair if not p.exists()]
    if missing:
        raise FileNotFoundError("training files missing:\n  " + "\n  ".join(map(str, missing)))


# Single year used for the dataset analysis figures, the run_all.py prediction check and the PDF.
REFERENCE_YEAR = 2025
REFERENCE_PAIR = (TRAIN_MODIS_DIR / MODIS_NAME.format(year=REFERENCE_YEAR),
                  TRAIN_VIIRS_DIR / VIIRS_NAME.format(year=REFERENCE_YEAR))

# ------------------------------------------------------------ outputs
OUT = HERE / "output"
ANALYSIS_DIR = OUT / "analysis"
MODEL_DIR = OUT / "models"
PRED_DIR = OUT / "predictions"
UNIFIED_DIR = OUT / "unified"

MODEL_FILE = MODEL_DIR / "hgb_modis_to_viirs.joblib"
SYNTH_STATS_FILE = MODEL_DIR / "synthesis_stats.joblib"
TRAIN_REPORT_FILE = MODEL_DIR / "training_report.json"
TRAIN_TABLE_FILE = MODEL_DIR / "training_table_full.pkl"


def set_train_years(years) -> None:
    """Override TRAIN_YEARS / TRAIN_PAIRS for this run (train.py --years)."""
    global TRAIN_YEARS, TRAIN_PAIRS
    TRAIN_YEARS = sorted(set(int(y) for y in years))
    TRAIN_PAIRS = [(TRAIN_MODIS_DIR / MODIS_NAME.format(year=y), TRAIN_VIIRS_DIR / VIIRS_NAME.format(year=y))
                   for y in TRAIN_YEARS]


def set_model_dir(path) -> None:
    """Write (and read) models in another folder for this run (train.py --out)."""
    global MODEL_DIR, MODEL_FILE, SYNTH_STATS_FILE, TRAIN_REPORT_FILE, TRAIN_TABLE_FILE
    MODEL_DIR = Path(path).resolve()
    MODEL_FILE = MODEL_DIR / "hgb_modis_to_viirs.joblib"
    SYNTH_STATS_FILE = MODEL_DIR / "synthesis_stats.joblib"
    TRAIN_REPORT_FILE = MODEL_DIR / "training_report.json"
    TRAIN_TABLE_FILE = MODEL_DIR / "training_table_full.pkl"
ANALYSIS_JSON = ANALYSIS_DIR / "dataset_analysis.json"
SCHEMA_CSV = ANALYSIS_DIR / "schema.csv"
UNIFIED_DAILY_CSV = UNIFIED_DIR / "unified_daily.csv"
UNIFIED_ROWS_CSV = UNIFIED_DIR / "unified_viirs_format.csv"
PDF_FILE = ROOT / "MODIS_VIIRS_ML_Architecture_v2.pdf"


def pred_file(kind: str, modis_csv: Path, out_dir: Path | None = None) -> Path:
    """predict.py outputs: kind = viirs_equivalent | daily_summary | daily_cells | summary."""
    ext = ".json" if kind == "summary" else ".csv"
    return Path(out_dir or PRED_DIR) / f"{kind}_{Path(modis_csv).stem}{ext}"

# ------------------------------------------------------------ space / time units
GRID_DEG = 0.1            # daily cell size (~11 km). 0.25 = fewer, fuller cells.
GRID_LAT0, GRID_LON0 = -60.0, 90.0   # grid origin (covers Australia)
DAY_START_HOUR = 6        # "fire day" = local solar day shifted 6 h: one day pass + following night
# Fixed origin of the day index inside cell keys. One origin for every year, so a cell/day/pass key
# is unique across years (per-file origins made 9 Jan 2012 and 9 Jan 2013 share a key).
# Must be at least 2 days before the earliest fire_date (MODIS starts Nov 2000).
DAY0 = "2000-01-01"
NEIGHBOUR_DAYS = 1        # candidate cells: MODIS within +-1 day ...
NEIGHBOUR_CELLS = 1       # ... and +-1 cell (3x3) in the same pass

AQUA_START = "2002-07-04"  # before this date only Terra exists -> Terra-only model

# ------------------------------------------------------------ model (XGBoost, see gbm.py)
# Same model family and settings as the earlier sklearn HistGradientBoosting version:
# histogram trees grown leaf-wise (lossguide), learning rate 0.06, L2 = 1, early stopping after
# 30 rounds without improvement on a held-out 10% of the training rows.
# min_child_weight is XGBoost's minimum leaf size measured in hessian, not rows (sklearn used
# min_samples_leaf=40 rows): for squared error hessian = weight, so 40 ~ 40 rows; for the Poisson
# count model hessian = predicted count, which is ~0.02 in empty cells, so 1.0 ~ 40-50 empty rows.
XGB_DEVICE = "cuda"        # "cuda" = NVIDIA GPU; falls back to "cpu" automatically if CUDA fails
VAL_FRACTION = 0.1
XGB_COMMON = dict(tree_method="hist", grow_policy="lossguide", max_depth=0, max_bin=256,
                  learning_rate=0.06, reg_lambda=1.0, seed=42, verbosity=1)
XGB_PARAMS = {
    "count": dict(objective="count:poisson", max_leaves=63, min_child_weight=1.0,
                  num_boost_round=2000, early_stopping_rounds=30),
    "reg":   dict(objective="reg:squarederror", max_leaves=31, min_child_weight=40.0,
                  num_boost_round=400, early_stopping_rounds=30),
    "conf":  dict(objective="multi:softprob", max_leaves=31, min_child_weight=1.0,
                  num_boost_round=300, early_stopping_rounds=30),
}
CV_FOLDS = 4               # GroupKFold, groups = calendar month (blocked in time)

# ------------------------------------------------------------ model improvements (Nov 2026)
# Each switch is OFF until improve_check.py shows it lowers the day-by-day error.
# The count-model settings for A/B/C are applied by improve_check.py or by editing XGB_PARAMS above:
#   A  num_boost_round 600 -> 2000      B  objective "reg:tweedie" (variance power 1.3)
#   C  max_leaves 63 -> 127
FEAT_FIRE_HISTORY = True       # 1+2  fire age, days since last / until next detection, 3x3 over +-3 days,
                               #      own cell over +-7 and +-15 days
FEAT_AQUA_GROWTH = True        # 3+4  Aqua vs Terra count and FRP (log ratio), Aqua-only 3x3 neighbourhood
                               #      (full model only: Terra-only has no Aqua)
FEAT_CLIMATOLOGY = False       # 5+7  Terra detections per cell x calendar month (all years), and Terra
                               #      detections in the cell 3-12 months earlier ("burned recently")
FEAT_OVERPASS_TIME = True      # 6    mean local solar time of each satellite's detections in the cell
COUNT_SPLIT_DAYNIGHT = False   # E    separate day-pass and night-pass count models
FEAT_WEATHER = False           # V9   NASA POWER daily weather (weather.py): temperature, humidity, wind, rain,
                               #      VPD, 7/30/90-day rain, dry-day run, temperature anomaly. Needs the
                               #      weather file(s) in WEATHER_DIR for every training AND prediction year.
                               #      False = V8 (no weather input).
CLIMATOLOGY_FILE = OUT / "features" / "cell_climatology.pkl"   # built once from all MODIS files
# NASA POWER daily weather (V9). Any *.csv / *.parquet in this folder, wide schema:
#   date, latitude, longitude, T2M, T2M_MAX, T2M_MIN, PRECTOTCORR, RH2M, WS10M, PS
WEATHER_DIR = DATA_DIR / "weather"
WEATHER_CACHE_DIR = OUT / "features" / "weather"     # rebuilt automatically when the input files change
WEATHER_MAX_MISSING_SHARE = 0.01   # stop if more than 1% of feature rows fall outside the weather dates

# ------------------------------------------------------------ training memory
# ~86% of candidate rows have no MODIS fire and no VIIRS fire. Keep this share of them (per year,
# seeded) and weight each kept one by 1/EMPTY_KEEP, so totals stay unbiased. 1.0 = keep all.
EMPTY_KEEP = 0.2
SAVE_TRAIN_TABLE = False   # write output/models/training_table_full.pkl (large; for inspection only)

# ------------------------------------------------------------ output rows (VIIRS CSV schema)
VIIRS_COLUMNS = ["latitude", "longitude", "bright_ti4", "scan", "track", "acq_date", "acq_time",
                 "satellite", "instrument", "confidence", "version", "bright_ti5", "frp",
                 "daynight", "type"]
OUT_SATELLITE = "N"               # emulating Suomi-NPP
OUT_VERSION = "ML-XGB-1.0"        # marks rows as model-generated (real FIRMS rows say 2.0)
RANDOM_SEED = 42
