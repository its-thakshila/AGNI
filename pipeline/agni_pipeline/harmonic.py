"""Harmonic: AGNI's MODIS -> VIIRS-equivalent harmonization model (adapter).

The model itself lives in pipeline/harmonic/ml_pipeline (the ML team's code, unchanged) with
its trained files in pipeline/harmonic/ml_pipeline/output/models/. This module only:
  1. turns FIRMS MODIS rows into the DataFrame their predict.py expects,
  2. runs their predict_cells() + synthesize_rows() (the same two steps as their gapfill.py),
  3. returns VIIRS-format rows (15 columns) for the requested fire-day.

To update the model, retrain with their train.py and commit the new files in output/models/
(see pipeline/harmonic/README.md). Nothing here needs to change.
"""

from __future__ import annotations

import sys
from datetime import date
from functools import lru_cache
from pathlib import Path

ML_DIR = Path(__file__).resolve().parents[1] / "harmonic" / "ml_pipeline"

# Real model if its files are present; the placeholder below is only a fallback for tests.
PLACEHOLDER = not (ML_DIR / "output" / "models" / "hgb_modis_to_viirs.joblib").exists()
MODEL_VERSION = "HARMONIC-PLACEHOLDER" if PLACEHOLDER else "ML-XGB-1.0-gapfill"

# The model's features look at MODIS detections up to 3 days before and after (own_7d_n,
# neighbouring days), so a fire-day is predicted from a 7-day MODIS window, like gapfill.py.
CONTEXT_DAYS = 3


@lru_cache(maxsize=1)
def _model():
    """Import the ML team's modules and load the trained model once per run."""
    if str(ML_DIR) not in sys.path:
        # their modules import each other by plain name ("import config"), and the saved model
        # files reference their module "gbm" by that name, so their folder must be importable
        sys.path.insert(0, str(ML_DIR))
    import common  # noqa: E402  (their modules)
    import config
    import predict

    bundles, stats = predict.load_models()
    return common, config, predict, bundles, stats


def model_version() -> str:
    if PLACEHOLDER:
        return MODEL_VERSION
    return _model()[1].OUT_VERSION + "-gapfill"


def gapfill_weights() -> tuple[list[float], list[float]] | None:
    """Per-month day/night weights from the trained model (VIIRS pixels the model can't place).

    These are the exact values behind the `weight` column of the processed data; None means
    use the rounded table in the backend builder.
    """
    if PLACEHOLDER:
        return None
    adjust = _model()[4].get("unreachable_adjust")
    if not adjust:
        return None
    return ([float(adjust.get(f"{m}_D", 1.0)) for m in range(1, 13)],
            [float(adjust.get(f"{m}_N", 1.0)) for m in range(1, 13)])


def _modis_frame(rows: list[dict]):
    import numpy as np
    import pandas as pd

    common, *_ = _model()
    df = pd.DataFrame(rows)
    for col in common.MODIS_COLUMNS:
        if col not in df:
            df[col] = 0 if col == "type" else np.nan  # FIRMS near-real-time files have no "type"
    df = df[common.MODIS_COLUMNS].copy()
    df["acq_date"] = df["acq_date"].astype(str)
    df["acq_time"] = df["acq_time"].astype(str).str.replace(r"\.0+$", "", regex=True).str.zfill(4)
    for col in ("latitude", "longitude", "brightness", "scan", "track", "bright_t31", "frp"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["type"] = pd.to_numeric(df["type"], errors="coerce").fillna(0).astype(int)
    df["satellite"] = df["satellite"].astype(str)
    df["confidence"] = pd.to_numeric(df["confidence"], errors="coerce").astype("float32")
    df["sensor"] = np.where(df["satellite"].str.upper().str.startswith("A"), "aqua", "terra")
    df = df.dropna(subset=["latitude", "longitude", "frp", "scan", "track"])
    return common.add_cell_keys(common.add_time_keys(df))


def predict(modis_rows: list[dict], fire_day: date) -> list[dict]:
    """MODIS FIRMS rows (fire_day +- CONTEXT_DAYS) -> VIIRS-equivalent rows for that window.

    The caller keeps the rows whose fire-day is `fire_day`. Values are strings, in the 15 VIIRS
    columns of the processed data.
    """
    if PLACEHOLDER:
        return _placeholder(modis_rows, fire_day)
    if not modis_rows:
        return []
    import numpy as np

    _, config, model, bundles, stats = _model()
    m = _modis_frame(modis_rows)
    if m.empty:
        return []
    X = model.predict_cells(m, bundles)
    if X.empty:
        return []
    rng = np.random.default_rng([config.RANDOM_SEED, fire_day.toordinal()])  # repeatable per day
    rows, _ = model.synthesize_rows(X, m, stats, rng)
    rows = rows.assign(version=model_version())
    return rows.astype(str).to_dict("records")


# ------------------------------------------------------------------ fallback for tests only
def _placeholder(modis_rows: list[dict], fire_day: date) -> list[dict]:
    """Not a model: ~4.5 VIIRS rows per MODIS row, scattered in its footprint. Used only when
    the trained model files are missing (e.g. a test checkout without them)."""
    import random

    rng = random.Random(f"harmonic-{fire_day}")
    out = []
    for row in modis_rows:
        lat, lon = float(row["latitude"]), float(row["longitude"])
        frp = float(row.get("frp") or 0)
        n = max(1, round(rng.gauss(4.5, 1.0)))
        conf = str(row.get("confidence") or "")
        conf = conf if conf in ("l", "n", "h") else ("h" if conf.replace(".", "", 1).isdigit() and float(conf) >= 80 else "n")
        for _ in range(n):
            out.append({
                "latitude": f"{lat + rng.uniform(-0.0045, 0.0045):.5f}", "longitude": f"{lon + rng.uniform(-0.0045, 0.0045):.5f}",
                "bright_ti4": f"{min(367.0, float(row.get('brightness') or 330)):.2f}", "scan": "0.39", "track": "0.41",
                "acq_date": row["acq_date"], "acq_time": str(row["acq_time"]).zfill(4), "satellite": "N", "instrument": "VIIRS",
                "confidence": conf, "version": MODEL_VERSION, "bright_ti5": f"{float(row.get('bright_t31') or 295):.2f}",
                "frp": f"{frp / n:.2f}", "daynight": row.get("daynight") or "D", "type": row.get("type") or "0",
            })
    return out
