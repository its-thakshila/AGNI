"""Orbit-drift check: does the Terra/Aqua overpass-time drift (2022+) affect the MODIS->VIIRS model?

Stand-alone analysis; does not change any model, prediction or unified file.
  1. overpass  : median daytime local solar time per year (Terra, Aqua, VIIRS) from the raw CSVs
  2. ratios    : real VIIRS / MODIS daytime detection ratio per year (2012-2025)
  3. loyo      : leave-one-year-out CV -> honest per-year error for every training year
  4. backcast  : test 2012-2013 with models trained on 2014-2021 (stable) vs 2014-2025 (incl. drift)
  5. forward   : train 2012-2021 (stable) -> predict 2023-2025 (drift)
Each model test is run for the full (Terra+Aqua) and terra_only models.

    python drift_check.py      ->  output/drift/drift_results.json  (~30 min, GPU)
"""
from __future__ import annotations

import json
import logging
import time

import numpy as np
import pandas as pd

import config
import features
import gbm
import train

log = logging.getLogger("drift")
OUT_DIR = config.OUT / "drift"
RESULTS = OUT_DIR / "drift_results.json"
STABLE = list(range(2012, 2022))      # overpass times within ~0.1 h of their nominal value
DRIFT = [2023, 2024, 2025]            # >0.3 h drift for both satellites


def _lst(df):
    t = df.acq_time.astype(str).str.zfill(4)
    return (t.str[:2].astype(int) + t.str[2:].astype(int) / 60 + df.longitude / 15) % 24


def overpass_and_ratios() -> list[dict]:
    rows = []
    for y in config.modis_years():
        m = pd.read_csv(config.modis_path(y), usecols=["longitude", "acq_time", "satellite", "daynight"],
                        dtype={"acq_time": str})
        m = m[m.daynight == "D"]
        m["sat"] = np.where(m.satellite.str.upper().str[0] == "A", "aqua", "terra")
        m["lst"] = _lst(m)
        r = {"year": y}
        for s, g in m.groupby("sat"):
            r[f"{s}_lst"] = float(g.lst.median())
            r[f"{s}_n_day"] = int(len(g))
        vp = config.viirs_path(y)
        if vp is not None:
            v = pd.read_csv(vp, usecols=["longitude", "acq_time", "daynight"], dtype={"acq_time": str})
            v = v[v.daynight == "D"]
            r["viirs_lst"] = float(_lst(v).median())
            r["viirs_n_day"] = int(len(v))
            for s in ("terra", "aqua"):
                if r.get(f"{s}_n_day"):
                    r[f"ratio_viirs_{s}"] = r["viirs_n_day"] / r[f"{s}_n_day"]
        rows.append(r)
        log.info("overpass %d: %s", y, {k: round(v, 2) for k, v in r.items() if k.endswith("lst")})
    return rows


def _score(X, p):
    s = train.scores(X.v_n, p, X.fire_date, X.w)
    for name, sel in (("day", X.is_night == 0), ("night", X.is_night == 1)):
        y, q, w = X.v_n[sel], p[sel.to_numpy()], X.w[sel]
        s[f"bias_{name}_pct"] = round(100 * (float((q * w).sum()) / float((y * w).sum()) - 1), 2)
    return s


def _fit_predict(X, feats, tr, te):
    mdl = gbm.fit_count(X.loc[tr, feats], X.loc[tr, "v_n"], X.loc[tr, "w"], feats)
    return np.clip(mdl.predict(X.loc[te, feats]), 0, None)


def model_tests(use_aqua: bool) -> dict:
    t0 = time.perf_counter()
    X, _ = train.build_training_table(use_aqua)
    X = X.reset_index(drop=True)
    feats = features.feature_names(use_aqua)
    year = X.fire_date.dt.year
    log.info("table %s: %d rows in %.0fs", "full" if use_aqua else "terra_only", len(X), time.perf_counter() - t0)
    out = {"loyo": {}, "backcast": {}, "forward": {}}

    for y in config.TRAIN_YEARS:
        te = year == y
        p = _fit_predict(X, feats, ~te, te)
        out["loyo"][y] = _score(X[te], p)
        log.info("loyo %d: %s", y, out["loyo"][y])

    te = year.isin([2012, 2013])
    for name, yrs in (("stable_2014_2021", range(2014, 2022)), ("all_2014_2025", range(2014, 2026))):
        p = _fit_predict(X, feats, year.isin(yrs), te)
        out["backcast"][name] = _score(X[te], p)
        log.info("backcast %s: %s", name, out["backcast"][name])

    te = year.isin(DRIFT)
    p = _fit_predict(X, feats, year.isin(STABLE), te)
    Xt = X[te]
    for y in DRIFT:
        s = (Xt.fire_date.dt.year == y).to_numpy()
        out["forward"][y] = _score(Xt[s], p[s])
        log.info("forward %d: %s", y, out["forward"][y])
    return out


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    res = {"overpass": overpass_and_ratios(), "stable_years": STABLE, "drift_years": DRIFT,
           "model": gbm.describe()}
    for mode, use_aqua in (("full", True), ("terra_only", False)):
        res[mode] = model_tests(use_aqua)
        RESULTS.write_text(json.dumps(res, indent=1, default=float))      # save progress
    log.info("saved %s", RESULTS)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    main()
