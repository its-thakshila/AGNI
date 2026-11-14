"""Train the gradient-boosted MODIS -> VIIRS(S-NPP) models (XGBoost, GPU when available; see gbm.py).

Models, each trained twice: full (Terra + Aqua) and
Terra-only (for 2000-07/2002, before Aqua):
  count  Poisson regression       -> expected VIIRS pixels per cell x day x pass
  frp    Regressor on log FRP     -> mean VIIRS FRP per pixel (cells with VIIRS)
  ti4    Regressor                -> mean VIIRS bright_ti4
  ti5    Regressor                -> mean VIIRS bright_ti5
  conf   Classifier l/n/h         -> VIIRS confidence class shares

Validation: GroupKFold over ISO weeks (whole weeks held out), compared with the
fixed-ratio and per-month-ratio baselines from the original architecture.
"""
from __future__ import annotations

import json
import logging
import time

import joblib
import numpy as np
import pandas as pd
import gbm
from sklearn.metrics import mean_absolute_error, mean_poisson_deviance, r2_score
from sklearn.model_selection import GroupKFold

import common
import config
import features
from vizstyle import INK_2, TERRA, VIIRS, plt, save

log = logging.getLogger("train")
CLASSES = np.array(["l", "n", "h"])


# ------------------------------------------------------------------ data
V_KEEP = ["key", "fire_date", "pass", "lst_hour", "scan", "track", "frp", "bright_ti4", "bright_ti5", "reachable"]
V_F32 = ["scan", "track", "frp", "bright_ti4", "bright_ti5"]


def build_training_table(use_aqua: bool):
    """One year at a time: features + VIIRS targets, drop no-VIIRS days, sample empty rows, shrink.

    Column `w` = row weight (1, or 1/EMPTY_KEEP for sampled empty rows); every sum over rows uses it.
    """
    tables, viirs_all = [], []
    for year, (mp, vp) in zip(config.TRAIN_YEARS, config.TRAIN_PAIRS):
        m, v = common.load_modis(mp), common.load_viirs(vp)
        X = features.build(m, use_aqua=use_aqua)
        T, vk = features.viirs_targets(v, X.attrs["day0"])
        X = X.join(T, on="key")
        X[["v_n", "v_frp_sum", "v_l", "v_nom", "v_h"]] = X[["v_n", "v_frp_sum", "v_l", "v_nom", "v_h"]].fillna(0)
        vk["reachable"] = np.isin(vk["key"].to_numpy(), X["key"].to_numpy())   # before any row is dropped
        n_cand, n_pos = len(X), int((X.v_n > 0).sum())

        # fire days with no VIIRS data at all (instrument not yet operating / outages): MODIS rows there
        # would be labelled "VIIRS saw 0" although VIIRS was not observing -> drop them
        no_viirs = ~X["fire_date"].isin(v["fire_date"].unique())
        n_drop, d_drop = int(no_viirs.sum()), X.loc[no_viirs, "fire_date"].nunique()
        X = X[~no_viirs]

        # sample cells where neither sensor saw fire; weight the kept ones so sums stay unbiased
        rng = np.random.default_rng(config.RANDOM_SEED + year)
        empty = ((X.modis_empty == 1) & (X.v_n == 0)).to_numpy()
        keep = ~empty | (rng.random(len(X)) < config.EMPTY_KEEP)
        X = X[keep].drop(columns=["key"])
        X["w"] = np.where(empty[keep], 1.0 / config.EMPTY_KEEP, 1.0)
        f64 = X.select_dtypes("float64").columns
        X[f64] = X[f64].astype("float32")

        vk = vk[V_KEEP].copy()
        vk[V_F32] = vk[V_F32].astype("float32")
        tables.append(X)
        viirs_all.append(vk)
        log.info("%s: %d candidate rows, %d with VIIRS, reachable VIIRS pixels %.1f%% | "
                 "dropped %d rows on %d no-VIIRS days | kept %d rows (%.0f%%), %.0f MB",
                 mp.name, n_cand, n_pos, 100 * vk.reachable.mean(), n_drop, d_drop,
                 len(X), 100 * len(X) / n_cand, X.memory_usage(deep=True).sum() / 1e6)
        del m, v, T
    X = pd.concat(tables, ignore_index=True)
    del tables
    X["week_group"] = X["fire_date"].dt.isocalendar().year * 100 + X["fire_date"].dt.isocalendar().week
    return X, pd.concat(viirs_all, ignore_index=True)


# ------------------------------------------------------------------ models
def fit_models(X: pd.DataFrame, feats: list[str]) -> dict:
    pos = X[X.v_n > 0]
    count = gbm.fit_count(X[feats], X["v_n"], X["w"], feats)
    wp = np.sqrt(pos["v_n"])
    frp = gbm.fit("reg", pos[feats], np.log(pos["v_frp_mean"].clip(lower=0.05)), wp)
    ti4 = gbm.fit("reg", pos[feats], pos["v_ti4"], wp)
    ti5 = gbm.fit("reg", pos[feats], pos["v_ti5"], wp)
    Xc, yc, wc = conf_long(pos, feats)
    conf = gbm.fit("conf", Xc, yc, wc, classes=CLASSES)
    # Duan smearing: exp(E[log FRP]) underestimates the mean; pixel-weighted retransformation factor
    smear = float((pos["v_frp_mean"] * pos["v_n"]).sum() / (np.exp(frp.predict(pos[feats])) * pos["v_n"]).sum())
    return {"count": count, "frp": frp, "ti4": ti4, "ti5": ti5, "conf": conf, "features": feats,
            "frp_smear": smear}


def conf_long(pos: pd.DataFrame, feats: list[str]):
    parts = []
    for col, cls in (("v_l", "l"), ("v_nom", "n"), ("v_h", "h")):
        w = pos[col].to_numpy()
        keep = w > 0
        parts.append((pos.loc[keep, feats], np.full(keep.sum(), cls), w[keep]))
    return (pd.concat([p[0] for p in parts]), np.concatenate([p[1] for p in parts]),
            np.concatenate([p[2] for p in parts]))


# ------------------------------------------------------------------ validation
def ratio_baselines(train: pd.DataFrame, test: pd.DataFrame):
    g = train.v_n.sum() / max(train.modis_n.sum(), 1)
    s = train.groupby("month")[["v_n", "modis_n"]].sum()
    mon = s.v_n / s.modis_n.clip(lower=1)
    r_m = test["month"].map(mon).fillna(g).to_numpy()
    return g * test.modis_n.to_numpy(), r_m * test.modis_n.to_numpy()


def scores(y, p, dates, w) -> dict:
    """Cell metrics weighted by w; daily / monthly totals use w-weighted sums (= all candidate rows)."""
    y, w = np.asarray(y, float), np.asarray(w, float)
    p = np.clip(np.asarray(p, float), 1e-6, None)
    daily = pd.DataFrame({"y": y * w, "p": p * w, "d": np.asarray(dates)}).groupby("d")[["y", "p"]].sum()
    monthly = daily.groupby(daily.index.to_period("M")).sum()
    return {
        "cell_r2": round(r2_score(y, p, sample_weight=w), 4),
        "cell_mae": round(mean_absolute_error(y, p, sample_weight=w), 3),
        "cell_poisson_dev": round(mean_poisson_deviance(y, p, sample_weight=w), 3),
        "daily_r2": round(r2_score(daily.y, daily.p), 4),
        "daily_mae_pct": round(100 * mean_absolute_error(daily.y, daily.p) / daily.y.mean(), 2),
        "monthly_r2": round(r2_score(monthly.y, monthly.p), 4),
        "monthly_mae_pct": round(100 * mean_absolute_error(monthly.y, monthly.p) / monthly.y.mean(), 2),
        "total_bias_pct": round(100 * (daily.p.sum() / daily.y.sum() - 1), 2),
    }


def cross_validate(X: pd.DataFrame, feats: list[str], with_secondary: bool):
    oof = np.zeros(len(X)); b_glob = np.zeros(len(X)); b_mon = np.zeros(len(X))
    sec = {"frp_r2_log": [], "ti4_mae_k": [], "ti5_mae_k": [], "conf_share_mae_pct": []}
    for k, (tr, te) in enumerate(GroupKFold(config.CV_FOLDS).split(X, groups=X["week_group"])):
        t0 = time.perf_counter()
        A, B = X.iloc[tr], X.iloc[te]
        mdl = gbm.fit_count(A[feats], A["v_n"], A["w"], feats)
        oof[te] = mdl.predict(B[feats])
        b_glob[te], b_mon[te] = ratio_baselines(A, B)
        if with_secondary:
            Ap, Bp = A[A.v_n > 0], B[B.v_n > 0]
            w = np.sqrt(Ap.v_n)
            f = gbm.fit("reg", Ap[feats], np.log(Ap.v_frp_mean.clip(lower=.05)), w)
            sec["frp_r2_log"].append(r2_score(np.log(Bp.v_frp_mean.clip(lower=.05)), f.predict(Bp[feats])))
            for c, key in (("v_ti4", "ti4_mae_k"), ("v_ti5", "ti5_mae_k")):
                r = gbm.fit("reg", Ap[feats], Ap[c], w)
                sec[key].append(mean_absolute_error(Bp[c], r.predict(Bp[feats]), sample_weight=Bp.v_n))
            Xc, yc, wc = conf_long(Ap, feats)
            clf = gbm.fit("conf", Xc, yc, wc, classes=CLASSES)
            proba = clf.predict_proba(Bp[feats])
            order = [list(clf.classes_).index(c) for c in CLASSES]
            pred_share = (proba[:, order] * Bp.v_n.to_numpy()[:, None]).sum(0) / Bp.v_n.sum()
            true_share = Bp[["v_l", "v_nom", "v_h"]].sum().to_numpy() / Bp.v_n.sum()
            sec["conf_share_mae_pct"].append(100 * np.abs(pred_share - true_share).mean())
        log.info("fold %d done in %.0fs (iters %d)", k + 1, time.perf_counter() - t0, mdl.n_iter_)
    res = {"hgb": scores(X.v_n, oof, X.fire_date, X.w),
           "baseline_fixed_ratio": scores(X.v_n, b_glob, X.fire_date, X.w),
           "baseline_monthly_ratio": scores(X.v_n, b_mon, X.fire_date, X.w)}
    if with_secondary:
        res["secondary"] = {k: round(float(np.mean(v)), 4) for k, v in sec.items()}
    return res, oof, b_mon


def apply_calib(X: pd.DataFrame, calib: dict) -> np.ndarray:
    k = X["modis_empty"].astype(int).astype(str) + "_" + X["is_night"].astype(int).astype(str)
    return k.map(calib).fillna(1.0).to_numpy()


# ------------------------------------------------------------------ synthesis statistics
def synthesis_stats(X: pd.DataFrame, V: pd.DataFrame) -> dict:
    rng = np.random.default_rng(config.RANDOM_SEED)
    q = np.linspace(0, 1, 201)
    lst = {p: np.quantile(V.loc[V["pass"] == p, "lst_hour"], q).round(3).tolist() for p in ("D", "N")}
    st = V.sample(min(30000, len(V)), random_state=config.RANDOM_SEED)[["scan", "track"]].round(2)
    g = V.groupby("key")
    logf = np.log(V["frp"].clip(lower=0.05))
    sd_logfrp = float((logf - logf.groupby(V["key"]).transform("mean")).std())
    sd_ti4 = float((V.bright_ti4 - g.bright_ti4.transform("mean")).std())
    sd_ti5 = float((V.bright_ti5 - g.bright_ti5.transform("mean")).std())
    # sensitivity remainder: VIIRS pixels with no MODIS fire within 3x3 cells / +-1 day
    V = V.assign(month=V["fire_date"].dt.month)
    reach = V.groupby(["month", "pass"])["reachable"].mean()
    adj = (1 / reach.clip(lower=0.5)).round(4)
    return {"lst_quantiles": lst, "scan_track": st.to_numpy().tolist(),
            "sd_logfrp": sd_logfrp, "sd_ti4": sd_ti4, "sd_ti5": sd_ti5,
            "ti4_range": [float(V.bright_ti4.min()), float(V.bright_ti4.max())],
            "ti5_range": [float(V.bright_ti5.min()), float(V.bright_ti5.max())],
            "unreachable_adjust": {f"{m}_{p}": float(v) for (m, p), v in adj.items()},
            "reachable_share_total": float(V["reachable"].mean())}


# ------------------------------------------------------------------ figures
def permutation_importance(model, F: pd.DataFrame, y: np.ndarray, repeats: int = 3) -> np.ndarray:
    """Mean increase in Poisson deviance when one feature column is shuffled."""
    rng = np.random.default_rng(config.RANDOM_SEED)
    dev = lambda D: mean_poisson_deviance(y, np.clip(model.predict(D), 1e-6, None))  # noqa: E731
    base = dev(F)
    out = []
    for c in F.columns:
        P = F.copy()
        out.append(np.mean([dev(P.assign(**{c: rng.permutation(P[c].to_numpy())})) - base
                            for _ in range(repeats)]))
    return np.array(out)


def validation_figures(X, oof, b_mon, model, feats):
    A = config.MODEL_DIR
    w = X.w.to_numpy(float)
    d = pd.DataFrame({"y": X.v_n * w, "ml": oof * w, "b": b_mon * w, "date": X.fire_date}).groupby("date").sum()
    fig, ax = plt.subplots(figsize=(8, 3.2))
    ax.plot(d.index, d.y, color=INK_2, linewidth=1.2, label="real VIIRS (held-out weeks)")
    ax.plot(d.index, d.ml, color=VIIRS, label="XGBoost prediction from MODIS")
    ax.plot(d.index, d.b, color=TERRA, linewidth=1.1, label="monthly-ratio baseline")
    ax.set_ylabel("VIIRS pixels per fire-day")
    ax.set_title("Out-of-fold daily VIIRS counts: XGBoost vs monthly-ratio rule")
    ax.legend(loc="upper left")
    save(fig, A / "fig_cv_daily.png")

    fig, axes = plt.subplots(1, 2, figsize=(8, 3.4), sharex=True, sharey=True)
    lim = max(d.y.max(), d.ml.max(), d.b.max()) * 1.05
    for ax, col, lab, c in ((axes[0], "ml", "XGBoost", VIIRS), (axes[1], "b", "monthly ratio", TERRA)):
        ax.scatter(d.y, d[col], s=10, color=c, alpha=0.7, edgecolors="none")
        ax.plot([0, lim], [0, lim], color=INK_2, linestyle="--", linewidth=1)
        ax.set_title(f"{lab}: daily R² = {r2_score(d.y, d[col]):.3f}")
        ax.set_xlabel("real VIIRS pixels / day")
    axes[0].set_ylabel("predicted / day")
    save(fig, A / "fig_cv_scatter.png")

    sample = X.sample(min(40000, len(X)), weights="w", random_state=config.RANDOM_SEED)   # = unsampled mix
    imp = pd.Series(permutation_importance(model, sample[feats], sample["v_n"].to_numpy(float)),
                    index=feats).sort_values().tail(15)
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.barh(imp.index, imp.values, color=VIIRS, height=0.6)
    ax.set_xlabel("permutation importance (increase in Poisson deviance)")
    ax.set_title("Top features of the VIIRS-count model")
    ax.grid(axis="y", visible=False)
    save(fig, A / "fig_feature_importance.png")
    return imp.sort_values(ascending=False).round(4).to_dict()


# ------------------------------------------------------------------ main
def main():
    config.check_train_files()
    config.MODEL_DIR.mkdir(parents=True, exist_ok=True)
    report = {}
    bundles = {}
    for mode, use_aqua in (("full", True), ("terra_only", False)):
        log.info("=== %s model ===", mode)
        X, V = build_training_table(use_aqua)
        feats = features.feature_names(use_aqua)
        cv, oof, b_mon = cross_validate(X, feats, with_secondary=(mode == "full"))
        # out-of-fold calibration: boosted trees shrink unseen weeks towards the mean, most for
        # MODIS-empty cells and night passes -> one multiplicative factor per (modis_empty, is_night)
        by = [X.modis_empty, X.is_night]
        calib = ((X.v_n * X.w).groupby(by).sum() / pd.Series(oof * X.w.to_numpy(), index=X.index).groupby(by).sum())
        calib = {f"{int(a)}_{int(b)}": round(float(v), 4) for (a, b), v in calib.items()}
        oof = oof * apply_calib(X, calib)
        cv["hgb_calibrated"] = scores(X.v_n, oof, X.fire_date, X.w)
        cv["calibration_factors"] = calib
        log.info("%s CV: %s", mode, json.dumps(cv))
        bundle = fit_models(X, feats)
        bundle["calib"] = calib
        bundles[mode] = bundle
        report[mode] = {"rows": len(X), "rows_represented": round(float(X.w.sum())),
                        "rows_with_viirs": int((X.v_n > 0).sum()), "empty_keep": config.EMPTY_KEEP,
                        "years": list(config.TRAIN_YEARS), "cv": cv,
                        "iterations": int(bundle["count"].n_iter_)}
        if mode == "full":
            report["synthesis"] = stats = synthesis_stats(X, V)
            report["full"]["feature_importance"] = validation_figures(X, oof, b_mon, bundle["count"], feats)
            joblib.dump(stats, config.SYNTH_STATS_FILE)
            if config.SAVE_TRAIN_TABLE:
                X.assign(cv_pred=oof).to_pickle(config.TRAIN_TABLE_FILE)
        del X, V
    joblib.dump(bundles, config.MODEL_FILE)
    rep = {k: v for k, v in report.items() if k != "synthesis"}
    rep["reachable_share_total"] = report["synthesis"]["reachable_share_total"]
    rep["unreachable_adjust"] = report["synthesis"]["unreachable_adjust"]
    rep["model"] = gbm.describe()
    config.TRAIN_REPORT_FILE.write_text(json.dumps(rep, indent=1, default=float))
    log.info("models + report saved to %s", config.MODEL_DIR)


def _parse_years(tokens: list[str]) -> list[int]:
    """"2012-2015" -> 2012..2015; "2019 2023" -> both; mix allowed."""
    years = []
    for t in tokens:
        for part in t.replace(",", " ").split():
            a, _, b = part.partition("-")
            years += list(range(int(a), int(b or a) + 1))
    return years


if __name__ == "__main__":
    import argparse
    import sys
    import threading

    ap = argparse.ArgumentParser(description="Train the MODIS -> VIIRS models. Without options: the years, "
                                             "folder and settings in config.py.")
    ap.add_argument("--years", nargs="+", help='e.g. 2012-2015  or  2019 2023 2025 (default: config.TRAIN_YEARS)')
    ap.add_argument("--out", help=f"output folder for models + report (default: {config.MODEL_DIR})")
    ap.add_argument("--empty-keep", type=float, help=f"share of empty cells kept (default: {config.EMPTY_KEEP})")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S",
                        stream=sys.stdout)
    if a.years:
        config.set_train_years(_parse_years(a.years))
    if a.out:
        config.set_model_dir(a.out)
    if a.empty_keep is not None:
        config.EMPTY_KEEP = a.empty_keep
    log.info("years %s | EMPTY_KEEP %g | output %s", list(config.TRAIN_YEARS), config.EMPTY_KEEP, config.MODEL_DIR)

    peak, done = [0], threading.Event()
    try:                                              # optional: peak memory of this process
        import psutil
        proc = psutil.Process()

        def _watch():
            while not done.wait(0.5):
                peak[0] = max(peak[0], proc.memory_info().rss)
        threading.Thread(target=_watch, daemon=True).start()
    except ImportError:
        proc = None
    t0 = time.time()
    main()
    done.set()
    mem = f"peak memory {peak[0] / 1e9:.2f} GB" if proc else "peak memory n/a (pip install psutil)"
    log.info("FINISHED: %d years in %.1f min, %s", len(config.TRAIN_YEARS), (time.time() - t0) / 60, mem)
