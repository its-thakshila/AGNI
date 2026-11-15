"""How reliable is comparing two days? Per-day out-of-sample predictions for 2012-2025.

Leave-one-year-out (each year predicted by a model trained on the other 13), then per fire-day:
real vs predicted VIIRS count (reachable part, same basis as training scores).
Does not change any model or output file.

    python day_compare_check.py   ->  output/drift/daily_loyo_<mode>.csv  (~25 min, GPU)
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

import config
import features
import train
from drift_check import OUT_DIR, _fit_predict

log = logging.getLogger("daycmp")


def run(mode: str, use_aqua: bool):
    X, _ = train.build_training_table(use_aqua)
    X = X.reset_index(drop=True)
    feats = features.feature_names(use_aqua)
    year = X.fire_date.dt.year
    p = np.zeros(len(X))
    for y in config.TRAIN_YEARS:
        te = year == y
        p[te.to_numpy()] = _fit_predict(X, feats, ~te, te)
        log.info("%s loyo %d done", mode, y)
    d = pd.DataFrame({"fire_date": X.fire_date, "night": X.is_night,
                      "real": X.v_n * X.w, "pred": p * X.w})
    out = d.pivot_table(index="fire_date", columns="night", values=["real", "pred"], aggfunc="sum").fillna(0)
    out.columns = [f"{a}_{'night' if b else 'day'}" for a, b in out.columns]
    out["real"] = out.real_day + out.real_night
    out["pred"] = out.pred_day + out.pred_night
    out.round(2).to_csv(OUT_DIR / f"daily_loyo_{mode}.csv")
    log.info("saved %s", OUT_DIR / f"daily_loyo_{mode}.csv")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for mode, ua in (("full", True), ("terra_only", False)):
        run(mode, ua)
