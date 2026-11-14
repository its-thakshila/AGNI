"""Test the model-improvement switches (config.py, Nov 2026) before turning any of them on.

Builds the training table once with every optional feature group, then trains the count model for
each variant, cumulatively, and scores it on held-out years (each predicted by a model trained on the
other 13 years). Does not change the trained model or any output file.

  V0  baseline (current settings)
  VA  + A  num_boost_round 2000
  V1  + 1/2 fire history          V2  + 3/4 Aqua growth        V3  + 5/7 climatology
  V4  + 6  overpass time          V5  + B  Tweedie objective   V6  + C  max_leaves 127
  V7  + E  separate day/night models

    python improve_check.py              ->  output/improve/improve_results.json  (~1-1.5 h, GPU)
    python improve_check.py --terra      also test the Terra-only model: baseline vs the best variant
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import time

import numpy as np

import config
import features
import gbm
import train
from drift_check import _score

log = logging.getLogger("improve")
OUT_DIR = config.OUT / "improve"
RESULTS = OUT_DIR / "improve_results.json"
TEST_YEARS = [2012, 2014, 2016, 2018, 2020, 2024]      # 5 normal-orbit years + 1 drift year
ALL_GROUPS = {"fire_history", "aqua_growth", "climatology", "overpass"}
BASE_COUNT = copy.deepcopy(config.XGB_PARAMS["count"])

VARIANTS = [   # name, label, feature groups, count-model settings, day/night split
    ("V0", "baseline",                     set(),                                   {},                                                  False),
    ("VA", "+A 2000 rounds",               set(),                                   {"num_boost_round": 2000},                           False),
    ("V1", "+fire history",                {"fire_history"},                        {"num_boost_round": 2000},                           False),
    ("V2", "+Aqua growth",                 {"fire_history", "aqua_growth"},         {"num_boost_round": 2000},                           False),
    ("V3", "+climatology",                 {"fire_history", "aqua_growth", "climatology"}, {"num_boost_round": 2000},                  False),
    ("V4", "+overpass time",               ALL_GROUPS,                              {"num_boost_round": 2000},                           False),
    ("V5", "+B Tweedie",                   ALL_GROUPS,                              {"num_boost_round": 2000, "objective": "reg:tweedie",
                                                                                     "tweedie_variance_power": 1.3},                     False),
    ("V6", "+C 127 leaves",                ALL_GROUPS,                              {"num_boost_round": 2000, "objective": "reg:tweedie",
                                                                                     "tweedie_variance_power": 1.3, "max_leaves": 127},  False),
    ("V7", "+E day/night models",          ALL_GROUPS,                              {"num_boost_round": 2000, "objective": "reg:tweedie",
                                                                                     "tweedie_variance_power": 1.3, "max_leaves": 127},  True),
    ("V8", "final: A+fire hist+Aqua+overpass", {"fire_history", "aqua_growth", "overpass"}, {"num_boost_round": 2000},              False),
]
FLAG = {"fire_history": "FEAT_FIRE_HISTORY", "aqua_growth": "FEAT_AQUA_GROWTH",
        "climatology": "FEAT_CLIMATOLOGY", "overpass": "FEAT_OVERPASS_TIME"}


def calibrated(X, preds: dict) -> dict:
    """Production-style calibration (factor per modis_empty x is_night), each held-out year calibrated
    with factors from the OTHER held-out years' predictions (never from its own)."""
    out = {}
    for y, (te, p) in preds.items():
        num, den = {}, {}
        for y2, (te2, p2) in preds.items():
            if y2 == y:
                continue
            B = X[te2]
            g = B.modis_empty.astype(int).astype(str) + "_" + B.is_night.astype(int).astype(str)
            df = np.c_[(B.v_n * B.w).to_numpy(), p2 * B.w.to_numpy()]
            for k in np.unique(g):
                m = (g == k).to_numpy()
                num[k] = num.get(k, 0.0) + df[m, 0].sum()
                den[k] = den.get(k, 0.0) + df[m, 1].sum()
        B = X[te]
        g = B.modis_empty.astype(int).astype(str) + "_" + B.is_night.astype(int).astype(str)
        f = g.map({k: num[k] / den[k] for k in num if den[k] > 0}).fillna(1.0).to_numpy()
        out[y] = _score(B, p * f)
    return out


def peak_gb() -> float | None:
    try:
        import psutil
        mi = psutil.Process().memory_info()
        return round(getattr(mi, "peak_wset", mi.rss) / 1e9, 2)
    except Exception:
        return None


def run_variants(use_aqua: bool, variants) -> dict:
    need = set().union(*(v[2] for v in variants))        # build the table once with every group needed
    for g, flag in FLAG.items():
        setattr(config, flag, g in need)
    t0 = time.perf_counter()
    X, _ = train.build_training_table(use_aqua)
    X = X.reset_index(drop=True)
    year = X.fire_date.dt.year
    log.info("table: %d rows, %d columns in %.0f s, peak %s GB", len(X), X.shape[1], time.perf_counter() - t0, peak_gb())

    out = {}
    for name, label, groups, params, split in variants:
        feats = features.feature_names(use_aqua, groups)
        config.XGB_PARAMS["count"] = {**BASE_COUNT, **params}
        res, t1 = {"label": label, "n_features": len(feats), "years": {}}, time.perf_counter()
        preds = {}
        for y in TEST_YEARS:
            te = (year == y).to_numpy()
            mdl = gbm.fit_count(X.loc[~te, feats], X.loc[~te, "v_n"], X.loc[~te, "w"], feats, split=split)
            p = np.clip(mdl.predict(X.loc[te, feats]), 0, None)
            s = _score(X[te], p)
            s["iterations"] = int(mdl.n_iter_)
            res["years"][y] = s
            preds[y] = (te, p)
        st = [y for y in TEST_YEARS if y < 2022]
        res["daily_err_normal"] = round(float(np.mean([res["years"][y]["daily_mae_pct"] for y in st])), 2)
        res["monthly_err_normal"] = round(float(np.mean([res["years"][y]["monthly_mae_pct"] for y in st])), 2)
        res["daily_err_2024"] = float(res["years"][2024]["daily_mae_pct"]) if 2024 in res["years"] else None
        res["cell_poisson_dev"] = round(float(np.mean([res["years"][y]["cell_poisson_dev"] for y in TEST_YEARS])), 3)
        cal = calibrated(X, preds)
        res["years_calibrated"] = cal
        res["cal_daily_err_normal"] = round(float(np.mean([cal[y]["daily_mae_pct"] for y in st])), 2)
        res["cal_monthly_err_normal"] = round(float(np.mean([cal[y]["monthly_mae_pct"] for y in st])), 2)
        res["cal_abs_bias_normal"] = round(float(np.mean([abs(cal[y]["total_bias_pct"]) for y in st])), 2)
        res["cal_daily_err_2024"] = float(cal[2024]["daily_mae_pct"]) if 2024 in cal else None
        res["minutes"], res["peak_gb"] = round((time.perf_counter() - t1) / 60, 1), peak_gb()
        out[name] = res
        log.info("%s %-22s daily %.2f%% (normal) | 2024 %.2f%% | monthly %.2f%% | iters %s | %.1f min",
                 name, label, res["daily_err_normal"], res["daily_err_2024"] or float("nan"),
                 res["monthly_err_normal"], [res["years"][y]["iterations"] for y in TEST_YEARS], res["minutes"])
        log.info("   calibrated: daily %.2f%% (normal) | 2024 %.2f%% | monthly %.2f%% | |bias| %.2f%%",
                 res["cal_daily_err_normal"], res["cal_daily_err_2024"] or float("nan"),
                 res["cal_monthly_err_normal"], res["cal_abs_bias_normal"])
        yield name, out
    config.XGB_PARAMS["count"] = BASE_COUNT


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--terra", action="store_true", help="also test the Terra-only model (baseline vs best)")
    ap.add_argument("--variants", help="only these variants, e.g. V0,V8, for BOTH models (stored under 'final_*')")
    a = ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if RESULTS.exists():                                    # keep earlier results: dated copy, never overwritten
        stamp = time.strftime("%Y%m%d_%H%M", time.localtime(RESULTS.stat().st_mtime))
        keep = OUT_DIR / f"improve_results_{stamp}.json"
        if not keep.exists():
            keep.write_text(RESULTS.read_text())
    results = json.loads(RESULTS.read_text()) if RESULTS.exists() else {}
    results.update({"test_years": TEST_YEARS, "model": gbm.describe()})
    if a.variants:
        v = {x[0]: x for x in VARIANTS}
        chosen = [v[n.strip()] for n in a.variants.split(",")]
        for mode, use_aqua in (("final_full", True), ("final_terra_only", False)):
            vs = chosen if use_aqua else [(n, l, g - {"aqua_growth"}, p, sp) for n, l, g, p, sp in chosen]
            results[mode] = {}
            for _, out in run_variants(use_aqua, vs):
                results[mode] = out
                RESULTS.write_text(json.dumps(results, indent=1, default=float))
        log.info("saved %s", RESULTS)
        return
    results["full"] = {}
    for _, out in run_variants(True, VARIANTS):
        results["full"] = out
        RESULTS.write_text(json.dumps(results, indent=1, default=float))
    if a.terra:
        full = results["full"]
        best = min((k for k in full if k != "V0"), key=lambda k: full[k]["daily_err_normal"])
        v = {n: (n, l, g - {"aqua_growth"}, p, s) for n, l, g, p, s in VARIANTS}
        results["terra_only"] = {}
        for _, out in run_variants(False, [v["V0"], v[best]]):
            results["terra_only"] = out
            RESULTS.write_text(json.dumps(results, indent=1, default=float))
    log.info("saved %s", RESULTS)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    main()
