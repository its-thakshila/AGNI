"""Train the hurdle XGBoost forecast models, one bundle per lead (models/lead_<L>.joblib).

  stage 1  occ   binary:logistic   any real VIIRS row on the target day (all rows, sampling weights)
  stage 2  cnt   count:poisson     row count, rows with fire only
  stage 3  frp / ti4 / ti5 (squared error) and conf (l/n/h softprob), rows with fire only
Since v5 (fcst_config.CV_BLOCKS set): year-block cross-validation. For each block of years, stages 1-2
are trained on the other blocks (early stopping on the block) and predict it, giving honest out-of-fold
predictions for every fitted year. From those:
  iso    a monotone probability correction (isotonic regression, weighted): fixes over-confident p
  calib  month x pass factors for the expected rows, from all years instead of one validation year
Then stages 1-2 are refitted on all fitted years with the mean best round count x CV_ROUNDS_FACTOR.
Without CV_BLOCKS (v1-v4): early stopping and calibration on the validation year. Test years are never
seen here.

An existing models/ folder is moved to models_archive/models_<time>[_name] first (never overwritten).

    python train_forecast.py                    # all leads in the tables
    python train_forecast.py --leads 1 --name history_only
    python train_forecast.py --final            # refit on every built year with the evaluated round counts
                                                # (build_dataset.py --with-test first) -> models_final/
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

import fcst_config as fc
import fmodels

log = logging.getLogger("train")
ATTR = ["frp_mean", "ti4_mean", "ti5_mean", "sh_l", "sh_n", "sh_h"]


def load_table(L: int, splits, cols, start=None, only=None, exclude=None) -> pd.DataFrame:
    """Rows of the given splits; `only` / `exclude` = (first year, last year) of the target day."""
    import pyarrow.dataset as ds
    d = ds.dataset(fc.TABLE_DIR / f"lead_{L}", format="parquet")
    f = ds.field("split").isin(list(splits))
    for blk, keep in ((only, True), (exclude, False)):
        if blk is not None:
            a = (pd.Timestamp(f"{blk[0]}-01-01") - pd.Timestamp(start)).days
            b = (pd.Timestamp(f"{blk[1]}-12-31") - pd.Timestamp(start)).days
            inside = (ds.field("tgt_d") >= a) & (ds.field("tgt_d") <= b)
            f = f & (inside if keep else ~inside)
    return d.to_table(columns=cols, filter=f).to_pandas()


def archive_models(model_dir: Path):
    if not model_dir.exists() or not any(model_dir.iterdir()):
        return
    rep = model_dir / "training_report.json"
    stamp = time.strftime("%Y%m%d_%H%M", time.localtime((rep if rep.exists() else model_dir).stat().st_mtime))
    name = (model_dir / "run_name.txt").read_text().strip() if (model_dir / "run_name.txt").exists() else ""
    dst = fc.ARCHIVE_DIR / f"{model_dir.name}_{stamp}{'_' + name if name else ''}"
    k = 1
    while dst.exists():
        dst = dst.with_name(f"{dst.name.split('__')[0]}__{k}"); k += 1
    fc.ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    shutil.move(str(model_dir), str(dst))
    log.info("previous models moved to %s", dst)


def wap(y, s, w) -> float:
    from sklearn.metrics import average_precision_score
    return float(average_precision_score(y, s, sample_weight=w))


def month_of(tgt_d, start) -> np.ndarray:
    return (pd.Timestamp(start) + pd.to_timedelta(tgt_d, unit="D")).month.to_numpy()


def train_lead(L: int, feats: list[str], start: str, final_rounds: dict | None = None,
               cv_attr_block=None) -> tuple[dict, dict]:
    """cv_attr_block: (CV refit) stages 1-2 with final_rounds on train + val; stage 3 early-stops on that block."""
    t0 = time.perf_counter()
    cols = feats + ["y", "w", "tgt_d"] + ATTR
    splits = ("train", "val") if cv_attr_block else ("train", "val", "test") if final_rounds else ("train",)
    tr = load_table(L, splits, cols)
    va = None if final_rounds else load_table(L, ("val",), cols)
    log.info("lead %d: train %d rows (%d with fire)%s", L, len(tr), int((tr.y > 0).sum()),
             "" if va is None else f", val {len(va)} rows ({int((va.y > 0).sum())} with fire)")
    R = final_rounds or {}

    def XY(df):
        return df[feats].to_numpy(np.float32)

    b = {"lead": L, "features": feats, "history_start": start, "version": fc.OUT_VERSION,
         "trained": pd.Timestamp.now().isoformat(timespec="seconds"), "final": bool(final_rounds)}
    rep = {"lead": L, "train_rows": len(tr), "train_fire_rows": int((tr.y > 0).sum())}

    # ---- stage 1: occurrence (all rows, weighted)
    Xtr = XY(tr)
    keep = ["y", "w", "tgt_d", "own_same_t", "is_night"] + ATTR     # free the feature columns (8 GB RAM)
    tr = tr[[c for c in keep if c in tr.columns]].copy()
    import gc
    gc.collect()
    if va is not None:
        Xva = XY(va)
        b["occ"] = fmodels.fit("occ", Xtr, (tr.y > 0), tr.w, Xva, (va.y > 0), va.w)
    else:
        b["occ"] = fmodels.fit("occ", Xtr, (tr.y > 0), tr.w, rounds=R["occ"])

    # ---- stage 2: rows if there is fire
    ptr = tr.y.to_numpy() > 0
    if va is not None:
        pva = va.y.to_numpy() > 0
        b["cnt"] = fmodels.fit("cnt", Xtr[ptr], tr.y[ptr], None, Xva[pva], va.y[pva], None)
    else:
        b["cnt"] = fmodels.fit("cnt", Xtr[ptr], tr.y[ptr], None, rounds=R["cnt"])

    # ---- stage 3: attributes (rows with fire and real attributes; capped random subset)
    rng = np.random.default_rng(fc.SEED)
    if cv_attr_block:            # CV refit: stage 3 early-stops on the last CV block (trained on the others)
        a0 = (pd.Timestamp(f"{cv_attr_block[0]}-01-01") - pd.Timestamp(start)).days
        a1 = (pd.Timestamp(f"{cv_attr_block[1]}-12-31") - pd.Timestamp(start)).days
        inb = ((tr.tgt_d >= a0) & (tr.tgt_d <= a1)).to_numpy()
    else:
        inb = None

    def attr_rows(df, X, cap, mask=None):     # indices only: no copy of the full table (8 GB RAM)
        ok = (df.y.to_numpy() > 0) & df.frp_mean.notna().to_numpy() & (df.frp_mean.to_numpy() > 0)
        k = np.flatnonzero(ok if mask is None else ok & mask)
        if len(k) > cap:
            k = np.sort(rng.choice(k, cap, replace=False))
        return df.iloc[k], X[k]
    if inb is not None:
        atr, Xatr = attr_rows(tr, Xtr, fc.ATTR_MAX_ROWS, ~inb)
        ava, Xava = attr_rows(tr, Xtr, fc.ATTR_MAX_ROWS // 4, inb)
        va = ava                                 # early stopping for stage 3 only
    else:
        atr, Xatr = attr_rows(tr, Xtr, fc.ATTR_MAX_ROWS)
        if va is not None:
            ava, Xava = attr_rows(va, Xva, fc.ATTR_MAX_ROWS // 4)
    for name, col, fn in (("frp", "frp_mean", np.log), ("ti4", "ti4_mean", None), ("ti5", "ti5_mean", None)):
        ytr_ = atr[col].to_numpy(); ytr_ = fn(ytr_) if fn else ytr_
        if va is not None:
            yva_ = ava[col].to_numpy(); yva_ = fn(yva_) if fn else yva_
            b[name] = fmodels.fit("reg", Xatr, ytr_, atr.y, Xava, yva_, ava.y)
        else:
            b[name] = fmodels.fit("reg", Xatr, ytr_, atr.y, rounds=R[name])
    res = np.log(atr.frp_mean.to_numpy()) - b["frp"].predict(Xatr)
    b["frp_smear"] = float(np.average(np.exp(res), weights=atr.y))      # log-model retransformation

    def rep3(df, X):        # one row per confidence class, weight = rows x share
        sh = df[["sh_l", "sh_n", "sh_h"]].to_numpy()
        lab = np.repeat(np.arange(3)[None], len(df), 0)
        w = sh * df.y.to_numpy()[:, None]
        k = w.ravel() > 0
        return np.repeat(X, 3, 0)[k], lab.ravel()[k], w.ravel()[k]
    Xc, yc, wc = rep3(atr, Xatr)
    if va is not None:
        Xcv, ycv, wcv = rep3(ava, Xava)
        b["conf"] = fmodels.fit("conf", Xc, yc, wc, Xcv, ycv, wcv)
    else:
        b["conf"] = fmodels.fit("conf", Xc, yc, wc, rounds=R["conf"])

    # ---- baselines (train rows, weighted): persistence rates
    own = tr.own_same_t.to_numpy(); y = tr.y.to_numpy(); w = tr.w.to_numpy()
    on = own > 0
    b["baselines"] = {"persistence": {
        "p1": float(np.average(y[on] > 0, weights=w[on])), "p0": float(np.average(y[~on] > 0, weights=w[~on])),
        "c1": float((w[on] * y[on]).sum() / max((w[on] * own[on]).sum(), 1e-9)),
        "m0": float((w[~on] * y[~on]).sum() / w[~on].sum())}}

    # ---- calibration + validation scores
    if va is not None and not cv_attr_block:
        mo = month_of(va.tgt_d.to_numpy(), start)
        P = fmodels.predict(b, va, mo, attrs=False)
        pas = np.where(va.is_night.to_numpy() == 1, "N", "D")
        g = pd.DataFrame({"k": pd.Series(mo).astype(str).to_numpy() + "_" + pas,
                          "a": va.y.to_numpy() * va.w.to_numpy(), "e": P.expected_rows.to_numpy() * va.w.to_numpy()})
        s = g.groupby("k")[["a", "e"]].sum()
        b["calib"] = ((s.a + fc.CALIB_SHRINK) / (s.e + fc.CALIB_SHRINK)).round(4).to_dict()
        P2 = fmodels.predict(b, va, mo, attrs=False)
        yb = va.y.to_numpy() > 0
        Bp = fmodels.baseline(b, va, "persistence")
        Bc = fmodels.baseline(b, va, "climatology")
        rep["val"] = {
            "rows": len(va), "fire_rows": int(yb.sum()),
            "pr_auc": {"model": wap(yb, P2.p_fire, va.w), "persistence": wap(yb, Bp.score, va.w),
                       "climatology": wap(yb, Bc.score, va.w)},
            "brier": {"model": float(np.average((P2.p_fire - yb) ** 2, weights=va.w)),
                      "persistence": float(np.average((Bp.p_fire - yb) ** 2, weights=va.w)),
                      "climatology": float(np.average((Bc.p_fire - yb) ** 2, weights=va.w))},
            "total_rows_actual": float((va.y * va.w).sum()),
            "total_rows_expected_before_calib": float(s.e.sum()),
            "total_rows_expected": float((P2.expected_rows * va.w).sum()),
            "frp_rmse_log": float(np.sqrt(np.average((np.log(ava.frp_mean) - b["frp"].predict(Xava)) ** 2,
                                                     weights=ava.y))),
            "ti4_rmse": float(np.sqrt(np.average((ava.ti4_mean - b["ti4"].predict(Xava)) ** 2, weights=ava.y))),
        }
    rep["rounds"] = {k: b[k].n_iter for k in ("occ", "cnt", "frp", "ti4", "ti5", "conf")}
    rep["importance"] = {k: b[k].importance(feats) for k in ("occ", "cnt")}
    rep["baselines"] = b["baselines"]
    rep["seconds"] = round(time.perf_counter() - t0)
    return b, rep


def fit_isotonic(p, y, w, bins: int = fc.ISO_BINS) -> list[list[float]]:
    """Monotone map raw p -> observed rate, fitted on weighted quantile bins of the out-of-fold predictions."""
    from sklearn.isotonic import IsotonicRegression
    o = np.argsort(p, kind="stable")
    p, y, w = p[o], y[o], w[o]
    cw = np.cumsum(w)
    k = np.minimum((cw / cw[-1] * bins).astype(np.int64), bins - 1)
    sw = np.bincount(k, w, bins)
    keep = sw > 0
    mp = np.bincount(k, w * p, bins)[keep] / sw[keep]
    rate = np.bincount(k, w * y, bins)[keep] / sw[keep]
    iso = IsotonicRegression(y_min=0, y_max=1, out_of_bounds="clip").fit(mp, rate, sample_weight=sw[keep])
    x = np.r_[0.0, mp, 1.0]
    return [x.tolist(), iso.predict(x).tolist()]


def train_lead_cv(L: int, feats: list[str], start: str) -> tuple[dict, dict]:
    import gc
    t0 = time.perf_counter()
    rep = {"lead": L, "cv_blocks": fc.CV_BLOCKS, "folds": []}
    cols = feats + ["y", "w", "tgt_d"]
    oof, rounds = [], {"occ": [], "cnt": []}
    for blk in fc.CV_BLOCKS:
        tr = load_table(L, ("train", "val"), cols, start, exclude=blk)
        Xtr, ytr, wtr = tr[feats].to_numpy(np.float32), tr.y.to_numpy(np.float32), tr.w.to_numpy(np.float32)
        del tr
        gc.collect()
        va = load_table(L, ("train", "val"), cols, start, only=blk)
        Xva, yva, wva = va[feats].to_numpy(np.float32), va.y.to_numpy(np.float32), va.w.to_numpy(np.float32)
        log.info("lead %d fold %s: train %d rows, held-out %d rows", L, blk, len(ytr), len(yva))
        m_occ = fmodels.fit("occ", Xtr, ytr > 0, wtr, Xva, yva > 0, wva)
        ptr, pva = ytr > 0, yva > 0
        m_cnt = fmodels.fit("cnt", Xtr[ptr], ytr[ptr], None, Xva[pva], yva[pva], None)
        del Xtr, ytr, wtr
        gc.collect()
        rounds["occ"].append(m_occ.n_iter)
        rounds["cnt"].append(m_cnt.n_iter)
        p, mu = np.clip(m_occ.predict(Xva), 0, 1), np.maximum(m_cnt.predict(Xva), 1.0)
        oof.append(pd.DataFrame({"p": p, "mu": mu, "y": yva, "w": wva, "tgt_d": va.tgt_d.to_numpy(),
                                 "night": va.is_night.to_numpy()}))
        rep["folds"].append({"block": blk, "rounds_occ": m_occ.n_iter, "rounds_cnt": m_cnt.n_iter,
                             "pr_auc": wap(pva, p, wva)})
        del va, Xva, m_occ, m_cnt
        gc.collect()
    O = pd.concat(oof, ignore_index=True)
    del oof
    yb = (O.y > 0).to_numpy()
    w = O.w.to_numpy()
    praw = O.p.to_numpy()

    # probability correction + month x pass factors from the out-of-fold years >= CALIB_MIN_YEAR
    # (unified targets before 2012 are Harmonic conversions with a different level: forecasts are for the
    # real-VIIRS era, so only those years set the calibration)
    yr = (pd.Timestamp(start) + pd.to_timedelta(O.tgt_d.to_numpy(), unit="D")).year.to_numpy()
    cy = yr >= fc.CALIB_MIN_YEAR
    iso = fit_isotonic(praw[cy], yb[cy].astype(np.float64), w[cy])
    pc = np.interp(praw, iso[0], iso[1]) + 1e-6 * praw          # tiny slope keeps the ranking
    mo = month_of(O.tgt_d.to_numpy(), start)
    key = pd.Series(mo).astype(str).to_numpy() + "_" + np.where(O.night.to_numpy() == 1, "N", "D")
    mu = O.mu.to_numpy()
    g = pd.DataFrame({"k": key, "a": O.y.to_numpy() * w, "e": pc * mu * w})[cy].groupby("k")[["a", "e"]].sum()
    calib = ((g.a + fc.CALIB_SHRINK) / (g.e + fc.CALIB_SHRINK)).round(4).to_dict()
    f = pd.Series(key).map(calib).to_numpy()
    T = pd.DataFrame({"yr": yr, "a": O.y.to_numpy() * w, "raw": praw * mu * w,
                      "cal": pc * mu * f * w}).groupby("yr").sum()

    def rel(pp):
        bb = np.minimum((pp * 10).astype(int), 9)
        return [[round(float(np.average(pp[bb == i], weights=w[bb == i])), 3),
                 round(float(np.average(yb[bb == i], weights=w[bb == i])), 3), int((bb == i).sum())]
                for i in range(10) if (bb == i).sum() > 100]
    rep["oof"] = {
        "pr_auc_raw": wap(yb, praw, w), "pr_auc_calibrated": wap(yb, pc, w),
        "brier_raw": float(np.average((praw - yb) ** 2, weights=w)),
        "brier_calibrated": float(np.average((pc - yb) ** 2, weights=w)),
        "reliability_raw_[p,observed,n]": rel(praw), "reliability_calibrated": rel(pc),
        "yearly_total_bias_pct_raw": {int(k): v for k, v in ((T.raw / T.a - 1) * 100).round(1).items()},
        "yearly_total_bias_pct_calibrated": {int(k): v for k, v in ((T.cal / T.a - 1) * 100).round(1).items()},
    }
    log.info("lead %d out-of-fold: PR-AUC %.3f, Brier %.6f -> %.6f; yearly total bias (calibrated) %s", L,
             rep["oof"]["pr_auc_raw"], rep["oof"]["brier_raw"], rep["oof"]["brier_calibrated"],
             list(rep["oof"]["yearly_total_bias_pct_calibrated"].values()))
    del O
    gc.collect()

    # refit on all fitted years
    R = {k: max(10, int(np.mean(v) * fc.CV_ROUNDS_FACTOR)) for k, v in rounds.items()}
    b, rep2 = train_lead(L, feats, start, final_rounds=R, cv_attr_block=fc.CV_BLOCKS[-1])
    b["iso"], b["calib"], b["cv"] = iso, calib, {"rounds": rounds, "final_rounds": R}
    rep.update({k: v for k, v in rep2.items() if k != "lead"})
    rep["seconds"] = round(time.perf_counter() - t0)
    return b, rep


def fc_cv(final: bool) -> bool:
    return bool(getattr(fc, "CV_BLOCKS", None)) and not final


def main(leads, name: str = "", final: bool = False):
    meta = json.loads((fc.TABLE_DIR / "meta.json").read_text())
    feats = meta["features"]
    leads = leads or meta["leads"]
    out = fc.HERE / ("models_final" if final else "models")
    final_rounds = {}
    if final:
        rep0 = json.loads((fc.MODEL_DIR / "training_report.json").read_text())
        final_rounds = {r["lead"]: {k: max(10, int(v * 1.1)) for k, v in r["rounds"].items()} for r in rep0["leads"]}
        calib0_full = {L: joblib.load(fc.MODEL_DIR / f"lead_{L}.joblib") for L in leads}
        calib0 = {L: calib0_full[L]["calib"] for L in leads}
    archive_models(out)                     # moves a non-empty folder; an empty one is reused
    out.mkdir(parents=True, exist_ok=True)
    reports = []
    for L in leads:
        if fc_cv(final):
            b, rep = train_lead_cv(L, feats, meta["history"][0])
        else:
            b, rep = train_lead(L, feats, meta["history"][0], final_rounds.get(L))
        b["clim_years"] = meta["clim_years"]
        b["weather_day"] = "target" if meta.get("weather") is True else (meta.get("weather") or False)
        if final:
            b["calib"] = calib0[L]
            if "iso" in calib0_full[L]:
                b["iso"] = calib0_full[L]["iso"]
        joblib.dump(b, out / f"lead_{L}.joblib", compress=3)
        reports.append(rep)
        if "val" in rep:
            v = rep["val"]
            log.info("lead %d val: PR-AUC model %.3f / persistence %.3f / climatology %.3f; rows %.0f actual vs "
                     "%.0f expected (%.0f before calibration)", L, v["pr_auc"]["model"], v["pr_auc"]["persistence"],
                     v["pr_auc"]["climatology"], v["total_rows_actual"], v["total_rows_expected"],
                     v["total_rows_expected_before_calib"])
    report = {"trained": pd.Timestamp.now().isoformat(timespec="seconds"), "device": fmodels.device(),
              "features": feats, "tables": meta, "final": final, "leads": reports}
    (out / "training_report.json").write_text(json.dumps(report, indent=1, default=float))
    if name:
        (out / "run_name.txt").write_text(name)
    log.info("models saved in %s", out)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--leads", type=int, nargs="+")
    ap.add_argument("--name", default="", help="label stored with the models (used in the archive folder name)")
    ap.add_argument("--final", action="store_true", help="refit on train + val (+ test) with fixed rounds")
    a = ap.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]*", a.name):
        ap.error("--name: letters, digits, _ . - only")
    main(a.leads, a.name, a.final)
