"""Full offline ML harmonization run.

    cd ml_pipeline
    python run_all.py

1. analyze_datasets.py  -> output/analysis/   (format + sensor-difference analysis, figures)
2. train.py             -> output/models/     (XGBoost models, CV report vs ratio baselines)
3. predict.py           -> output/predictions/ (config.REFERENCE_YEAR MODIS -> VIIRS-format rows +
                                                daily tables, compared with that year's real VIIRS file)
4. unify.py             -> output/unified/    (multi-year daily record; real VIIRS where it exists)
"""
import logging

import analyze_datasets
import config
import predict
import train
import unify

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    analyze_datasets.main()
    train.main()
    mp, vp = config.REFERENCE_PAIR
    predict.run(mp, vp)
    unify.main(write_rows=False)
