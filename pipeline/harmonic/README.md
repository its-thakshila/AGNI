# Harmonic (MODIS → VIIRS-equivalent model)

This folder belongs to the ML team. It holds Harmonic's code and trained model, exactly as the
ML pipeline produces them. The model in `output/models/` is **Harmonic v8** (trained 14 Nov 2026,
`python train_all.py --name v8`): XGBoost, 2000 rounds, with the fire-history, Aqua-growth and
overpass-time feature groups (`FEAT_WEATHER` stays off: weather is the experimental V9).
Cross-validated error: ±3.1% monthly / ±10.7% daily (Terra + Aqua), ±4.8% / ±15.0% (Terra only).

The data pipeline (`pipeline/agni_pipeline/`) uses it only on days when no VIIRS satellite has
data in NASA FIRMS: it fetches MODIS from 30 days before to 15 days after that day (the window v8's
fire-history features read, `harmonic.CONTEXT_BEFORE/AFTER`), runs `predict.predict_cells()` and
`predict.synthesize_rows()` (the same two steps as `gapfill.py`), and stores the rows with version
`ML-XGB-1.0-gapfill` (origin `gapfill`). A day estimated before its 15 following days existed is
estimated again once they do.

```
ml_pipeline/
  config.py common.py features.py gbm.py predict.py   ← used at prediction time
  train.py vizstyle.py drift_check.py improve_check.py … ← training and evaluation (offline)
  output/models/hgb_modis_to_viirs.joblib             ← trained models (loaded by the pipeline)
  output/models/synthesis_stats.joblib                ← row-synthesis statistics
  output/models/training_report.json                  ← CV scores, calibration, unreachable_adjust
  (output/features/cell_climatology.pkl is rebuilt automatically if FEAT_CLIMATOLOGY is ever switched on)
```

The data build (unified CSV → per-year Parquet) lives in `pipeline/data_build/`;
see `DATA_BUILD_GUIDE.md` there.

## Updating the model

1. Put the training CSVs in `pipeline/harmonic/data/` as `config.py` describes
   (`data/train/MODIS`, `data/train/VIIRS`, `data/predict`). They are git-ignored.
2. From `pipeline/harmonic/ml_pipeline/`, retrain as usual: `python train.py`
   (writes to `output/models/`, the folder the pipeline loads).
3. Check `output/models/training_report.json`, then commit the three files in
   `output/models/`.
4. If the model's output version changes, set `OUT_VERSION` in `config.py`; gap-filled rows are
   tagged `OUT_VERSION + "-gapfill"` automatically.

Nothing in `pipeline/agni_pipeline/` needs to change, as long as `predict.py` keeps
`load_models()`, `predict_cells(m, bundles)` and `synthesize_rows(X, m, stats, rng)`.
The next pipeline run (every 3 hours, or on demand) uses the new model for new gap days.

To re-predict days already stored, run the "Process new fire-days" workflow with those days
in the *days* box.

## Requirements

Prediction needs `pandas`, `numpy`, `xgboost` and `joblib` (in `pipeline/requirements.txt`).
Training additionally needs `scikit-learn` and `matplotlib` (see `ml_pipeline/requirements.txt`).
Keep `xgboost` in `pipeline/requirements.txt` at the version the model was trained with
(`training_report.json` → `"model"`).
