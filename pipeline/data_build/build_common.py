"""Shared settings for the data build scripts (build_parquet.py, build_aggregates.py, verify_build.py).

Every rule here must match ml_pipeline/common.py and ml_pipeline/config.py, so the API groups
detections into the same days, passes and cells as the model did.
"""
from __future__ import annotations

from pathlib import Path

# ------------------------------------------------------------------ default paths
# This folder is pipeline/data_build; the ML pipeline that writes the unified CSV is
# pipeline/harmonic/ml_pipeline. Output goes to pipeline/data_build/data (git-ignored).
HERE = Path(__file__).resolve().parent
ML_PIPELINE = HERE.parent / "harmonic" / "ml_pipeline"
DEFAULT_CSV = ML_PIPELINE / "output" / "unified" / "unified_viirs_format_gapfilled.csv"
DEFAULT_DAILY_CSV = ML_PIPELINE / "output" / "unified" / "unified_daily_gapfilled.csv"
DEFAULT_OUT = HERE / "data"

# ------------------------------------------------------------------ record
RECORD_START = "2000-11-01"      # first fire-day served
LOCAL_END = "2025-12-31"         # last fire-day served from local data (FIRMS after this)
MIN_DAY_ROWS = 100               # fewer rows Australia-wide on a day -> "partial"

# ------------------------------------------------------------------ time rules (ml_pipeline/common.py)
DAY_START_HOUR = 6               # fire_date = date(local solar time - 6 h)
# pass = 'D' if 6 <= lst_hour < 18 else 'N'

# ------------------------------------------------------------------ grid (ml_pipeline/config.py)
GRID_DEG = 0.1
GRID_LAT0, GRID_LON0 = -60.0, 90.0   # ci = floor((lat - LAT0) / 0.1), cj = floor((lon - LON0) / 0.1)

# ------------------------------------------------------------------ the 15 VIIRS CSV columns and their types
CSV_COLUMNS = {
    "latitude": "DOUBLE", "longitude": "DOUBLE", "bright_ti4": "DOUBLE",
    "scan": "DOUBLE", "track": "DOUBLE", "acq_date": "DATE", "acq_time": "VARCHAR",
    "satellite": "VARCHAR", "instrument": "VARCHAR", "confidence": "VARCHAR",
    "version": "VARCHAR", "bright_ti5": "DOUBLE", "frp": "DOUBLE",
    "daynight": "VARCHAR", "type": "INTEGER",
}

# ------------------------------------------------------------------ weights for model rows (ml + gapfill)
# From backend architecture v2, section 4.2 (training_report.json -> unreachable_adjust).
# Index 0 = January. Real rows always have weight 1.0.
WEIGHTS = {
    "D": [1.103, 1.161, 1.179, 1.114, 1.066, 1.059, 1.056, 1.065, 1.051, 1.037, 1.060, 1.082],
    "N": [1.144, 1.244, 1.498, 1.518, 1.479, 1.621, 1.644, 1.368, 1.196, 1.101, 1.111, 1.118],
}

# ------------------------------------------------------------------ uncertainty (typical daily error) per origin and period
UNCERTAINTY = [
    # origin,    from,         to,           value
    ("real",    "2000-01-01", "2099-12-31", 0.0),
    ("ml",      "2000-01-01", "2002-07-03", 0.16),   # Terra-only model
    ("ml",      "2002-07-04", "2099-12-31", 0.11),   # Terra + Aqua model
    ("gapfill", "2000-01-01", "2021-12-31", 0.11),
    ("gapfill", "2022-01-01", "2099-12-31", 0.13),   # MODIS orbit drift years
]
DAYNIGHT_UNRELIABLE_FROM = "2022-01-01"   # gapfill days from here: day/night split unreliable

ORIGINS = ("real", "ml", "gapfill")


def weights_values_sql() -> str:
    """The weight table as a SQL VALUES list: (month, pass, weight)."""
    rows = [f"({m + 1}, '{p}', {w})" for p, ws in WEIGHTS.items() for m, w in enumerate(ws)]
    return "VALUES " + ", ".join(rows)


def duckdb_connect(memory: str, threads: int | None, temp_dir: Path):
    import duckdb
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{memory}'")
    con.execute(f"SET temp_directory = '{temp_dir.as_posix()}'")
    con.execute("SET preserve_insertion_order = false")
    if threads:
        con.execute(f"SET threads = {int(threads)}")
    return con
