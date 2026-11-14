"""Apply the trained models to any MODIS CSV (e.g. modis_2005_Australia.csv).

Outputs (output/predictions/):
  viirs_equivalent_<name>.csv  one row per predicted VIIRS pixel, *same columns as the
                               FIRMS VIIRS S-NPP CSV*; version = config.OUT_VERSION marks model rows
  daily_summary_<name>.csv     one row per fire-day: MODIS counts, VIIRS-equivalent counts / FRP
  daily_cells_<name>.csv       one row per 0.1 deg cell x fire-day x pass

    python predict.py --year 2005          # MODIS file found via config (data/predict or data/train/MODIS)
    python predict.py --year 2025          # also compares with the real VIIRS file when one exists
    python predict.py --modis some/other/modis_file.csv [--viirs some/viirs_file.csv]
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

import common
import config
import features

log = logging.getLogger("predict")
CLS = np.array(["l", "n", "h"])


def load_models():
    return joblib.load(config.MODEL_FILE), joblib.load(config.SYNTH_STATS_FILE)


def predict_cells(m: pd.DataFrame, bundles: dict) -> pd.DataFrame:
    """Feature table + model outputs for every candidate cell x day x pass."""
    aqua_start = pd.Timestamp(config.AQUA_START)
    day0 = features.DAY0                                         # same fixed origin as training keys
    parts = []
    for mode, sel, use_aqua in (("terra_only", m["fire_date"] < aqua_start, False),
                                ("full", m["fire_date"] >= aqua_start, True)):
        if not sel.any():
            continue
        b = bundles[mode]
        # build exactly the optional feature groups this saved model was trained with
        X = features.build(m[sel], use_aqua=use_aqua, day0=day0, groups=features.wanted_groups(b["features"]))
        X = X[(X.fire_date < aqua_start) if mode == "terra_only" else (X.fire_date >= aqua_start)].copy()
        F = X[b["features"]]
        X["mu"] = np.clip(b["count"].predict(F), 0, None)
        if "calib" in b:                                    # out-of-fold bias calibration
            k = X["modis_empty"].astype(int).astype(str) + "_" + X["is_night"].astype(int).astype(str)
            X["mu"] *= k.map(b["calib"]).fillna(1.0).to_numpy()
        X["frp_mean"] = np.exp(b["frp"].predict(F)) * b.get("frp_smear", 1.0)
        X["ti4"] = b["ti4"].predict(F)
        X["ti5"] = b["ti5"].predict(F)
        proba = b["conf"].predict_proba(F)
        order = [list(b["conf"].classes_).index(c) for c in CLS]
        X[["p_l", "p_n", "p_h"]] = proba[:, order]
        X["model"] = mode
        X["day0"] = day0
        parts.append(X)
    X = pd.concat(parts, ignore_index=True)
    # neighbourhood rows extend +-1 day past the input, and the last UTC day already holds the next
    # local morning's Terra pass: keep fire-days inside the file's UTC date range
    lo, hi = pd.Timestamp(m.acq_date.min()), pd.Timestamp(m.acq_date.max())
    X = X[(X.fire_date >= lo) & (X.fire_date <= hi)].reset_index(drop=True)
    # mixed years (e.g. 2002, Aqua starts mid-year) concat terra-only parts with no aqua columns -> NaN
    for c in ("terra_n", "terra_frp_sum", "aqua_n", "aqua_frp_sum"):
        X[c] = X[c].fillna(0.0) if c in X else 0.0
    if "aqua_static_share" not in X:
        X["aqua_static_share"] = np.nan
    return X


def _rank_align(group: np.ndarray, driver: np.ndarray, values: np.ndarray) -> np.ndarray:
    """Permute `values` inside each group so their rank order follows `driver`."""
    out = np.empty_like(values)
    od = np.lexsort((driver, group))
    ov = np.lexsort((values, group))
    out[od] = values[ov]
    return out


def synthesize_rows(X: pd.DataFrame, m: pd.DataFrame, stats: dict, rng) -> pd.DataFrame:
    """Turn expected cell counts into VIIRS-format pixel rows."""
    n = np.floor(X["mu"].to_numpy()).astype(int)
    n += rng.random(len(X)) < (X["mu"].to_numpy() - n)          # stochastic rounding keeps totals
    X = X.assign(n_rows=n)
    C = X[X.n_rows > 0].reset_index(drop=True)
    cell = np.repeat(np.arange(len(C)), C["n_rows"].to_numpy())
    N = len(cell)
    if N == 0:
        return pd.DataFrame(columns=config.VIIRS_COLUMNS), X

    # ---- position: inside the footprint of a MODIS pixel of the same cell/day/pass, else in the cell
    mm = m[["latitude", "longitude", "scan", "track"]].copy()
    mm["key"] = features.encode(m.ci, m.cj, (m.fire_date - C.day0.iloc[0]).dt.days,
                                (m["pass"] == "N").astype(int))
    mm = mm.sort_values("key")
    uk, start, cnt = np.unique(mm["key"].to_numpy(), return_index=True, return_counts=True)
    ck = C["key"].to_numpy()[cell]
    pos = np.clip(np.searchsorted(uk, ck), 0, len(uk) - 1)
    has = uk[pos] == ck
    pick = start[pos] + np.floor(rng.random(N) * cnt[pos]).astype(int)
    lat_m, lon_m = mm["latitude"].to_numpy()[pick], mm["longitude"].to_numpy()[pick]
    scan_m, track_m = mm["scan"].to_numpy()[pick], mm["track"].to_numpy()[pick]
    clat = C["lat"].to_numpy()[cell]
    clon = C["lon"].to_numpy()[cell]
    half = config.GRID_DEG / 2
    dy = (rng.random(N) - .5) * track_m / 111.32
    dx = (rng.random(N) - .5) * scan_m / (111.32 * np.cos(np.radians(lat_m)))
    lat = np.where(has, lat_m + dy, clat + (rng.random(N) - .5) * 2 * half)
    lon = np.where(has, lon_m + dx, clon + (rng.random(N) - .5) * 2 * half)

    # ---- overpass time: S-NPP local-solar-time climatology for the pass -> UTC date/time
    is_n = C["is_night"].to_numpy()[cell].astype(bool)
    qs = np.linspace(0, 1, 201)
    u = rng.random(N)
    lst = np.where(is_n, np.interp(u, qs, stats["lst_quantiles"]["N"]), np.interp(u, qs, stats["lst_quantiles"]["D"]))
    hours_after = config.DAY_START_HOUR + np.mod(lst - config.DAY_START_HOUR, 24)
    fdate = C["fire_date"].to_numpy()[cell]
    utc = pd.to_datetime(fdate) + pd.to_timedelta(hours_after - lon / 15.0, unit="h")
    utc = utc.floor("min")

    # ---- radiometry: cell means from the models + within-cell spread from training data
    w = np.exp(rng.normal(0, stats["sd_logfrp"], N))
    w = w / pd.Series(w).groupby(cell).transform("mean").to_numpy()
    frp = np.clip(C["frp_mean"].to_numpy()[cell] * w, 0.1, None)
    lo4, hi4 = stats["ti4_range"]; lo5, hi5 = stats["ti5_range"]
    ti4 = np.clip(C["ti4"].to_numpy()[cell] + _rank_align(cell, frp, rng.normal(0, stats["sd_ti4"], N)), lo4, hi4)
    ti5 = np.clip(C["ti5"].to_numpy()[cell] + _rank_align(cell, frp, rng.normal(0, stats["sd_ti5"], N)), lo5, hi5)
    P = C[["p_l", "p_n", "p_h"]].to_numpy()[cell]
    code = (rng.random(N)[:, None] > np.cumsum(P, axis=1)[:, :2]).sum(1)      # 0=l 1=n 2=h
    code = _rank_align(cell, frp, code)                                         # brightest -> h
    st = np.asarray(stats["scan_track"])[rng.integers(0, len(stats["scan_track"]), N)]
    static = np.fmax(C["terra_static_share"].to_numpy(), C["aqua_static_share"].to_numpy())
    ftype = np.where(np.nan_to_num(static)[cell] > 0.5, 2, 0)

    rows = pd.DataFrame({
        "latitude": lat.round(5), "longitude": lon.round(5), "bright_ti4": ti4.round(2),
        "scan": st[:, 0], "track": st[:, 1],
        "acq_date": utc.strftime("%Y-%m-%d"), "acq_time": utc.strftime("%H%M"),
        "satellite": config.OUT_SATELLITE, "instrument": "VIIRS", "confidence": CLS[code],
        "version": config.OUT_VERSION, "bright_ti5": ti5.round(2), "frp": frp.round(2),
        "daynight": np.where(is_n, "N", "D"), "type": ftype,
    })[config.VIIRS_COLUMNS]
    return rows.sort_values(["acq_date", "acq_time"], kind="mergesort").reset_index(drop=True), X


def daily_tables(X: pd.DataFrame, stats: dict, viirs: pd.DataFrame | None):
    adj = stats["unreachable_adjust"]
    key = X["month"].astype(str) + "_" + np.where(X["is_night"] == 1, "N", "D")
    X = X.assign(mu_adj=X["mu"] * key.map(adj).fillna(1.0).to_numpy(),
                 frp_exp=X["mu"] * X["frp_mean"], pass_=np.where(X["is_night"] == 1, "N", "D"))
    cells = X[(X.mu >= 0.05) | (X.n_rows > 0) | (X.modis_n > 0)]
    lat, lon = common.cell_center(cells.ci, cells.cj)
    cells_out = pd.DataFrame({
        "fire_date": cells.fire_date.dt.strftime("%Y-%m-%d"), "pass": cells.pass_,
        "cell_lat": lat.round(3), "cell_lon": lon.round(3),
        "modis_terra_n": cells.terra_n.astype(int), "modis_aqua_n": cells.aqua_n.astype(int),
        "modis_frp_sum": cells.modis_frp.round(2),
        "viirs_equiv_n": cells.mu.round(3), "viirs_equiv_rows": cells.n_rows,
        "viirs_equiv_frp_sum": cells.frp_exp.round(2),
        "viirs_ti4_mean": cells.ti4.round(2), "p_conf_l": cells.p_l.round(3),
        "p_conf_n": cells.p_n.round(3), "p_conf_h": cells.p_h.round(3), "model": cells.model,
    })
    g = X.groupby("fire_date")
    daily = pd.DataFrame({
        "modis_terra_n": g.terra_n.sum().astype(int), "modis_aqua_n": g.aqua_n.sum().astype(int),
        "modis_frp_sum": g.modis_frp.sum().round(1),
        "viirs_equiv_n": g.mu.sum().round(1),
        "viirs_equiv_n_day": X[X.is_night == 0].groupby("fire_date").mu.sum().round(1),
        "viirs_equiv_n_night": X[X.is_night == 1].groupby("fire_date").mu.sum().round(1),
        "viirs_equiv_n_sensitivity_adj": g.mu_adj.sum().round(1),
        "viirs_equiv_rows": g.n_rows.sum(),
        "viirs_equiv_frp_sum": g.frp_exp.sum().round(1),
        "model": g.model.first(),
    }).fillna({"viirs_equiv_n_day": 0, "viirs_equiv_n_night": 0})
    if viirs is not None:
        gv = viirs.groupby("fire_date")
        daily["viirs_actual_n"] = gv.size()
        daily["viirs_actual_frp_sum"] = gv.frp.sum().round(1)
        daily[["viirs_actual_n", "viirs_actual_frp_sum"]] = daily[["viirs_actual_n", "viirs_actual_frp_sum"]].fillna(0)
    daily.index = daily.index.strftime("%Y-%m-%d")
    daily.index.name = "fire_date"
    return daily.reset_index(), cells_out


def run(modis_path: Path, viirs_path: Path | None = None, out_dir: Path | None = None, rows: bool = True):
    out_dir = Path(out_dir or config.PRED_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = Path(modis_path).stem
    bundles, stats = load_models()
    rng = np.random.default_rng(config.RANDOM_SEED)

    m = common.load_modis(modis_path)
    log.info("%s: %d MODIS detections %s .. %s", name, len(m), m.acq_date.min(), m.acq_date.max())
    X = predict_cells(m, bundles)
    R, X = synthesize_rows(X, m, stats, rng)
    v = common.load_viirs(viirs_path) if viirs_path else None
    daily, cells = daily_tables(X, stats, v)

    daily.to_csv(config.pred_file("daily_summary", modis_path, out_dir), index=False)
    cells.to_csv(config.pred_file("daily_cells", modis_path, out_dir), index=False)
    if rows:
        R.to_csv(config.pred_file("viirs_equivalent", modis_path, out_dir), index=False)
    summary = {"modis_rows": len(m), "viirs_equiv_expected": float(X.mu.sum()),
               "viirs_equiv_rows": int(len(R)), "days": int(len(daily))}
    if v is not None:
        d = daily
        summary.update({
            "viirs_actual_rows": int(len(v)),
            "daily_r2_vs_actual": float(1 - ((d.viirs_actual_n - d.viirs_equiv_n_sensitivity_adj) ** 2).sum()
                                        / ((d.viirs_actual_n - d.viirs_actual_n.mean()) ** 2).sum()),
            "note": "in-sample if this year was used for training; use training_report.json CV for honest scores",
        })
    config.pred_file("summary", modis_path, out_dir).write_text(json.dumps(summary, indent=1))
    log.info("%s", summary)
    return daily, cells, R


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--year", type=int, help="look up modis_<YEAR> (and viirs-snpp_<YEAR>) via config.py")
    src.add_argument("--modis", type=Path, help="explicit MODIS CSV path")
    ap.add_argument("--viirs", type=Path, help="optional real VIIRS CSV of the same year for comparison")
    ap.add_argument("--out", type=Path, help=f"output folder (default {config.PRED_DIR})")
    ap.add_argument("--no-rows", action="store_true", help="skip the pixel-level VIIRS-format CSV")
    a = ap.parse_args()
    if a.year is not None:
        modis, viirs = config.modis_path(a.year), a.viirs or config.viirs_path(a.year)
    else:
        modis, viirs = a.modis, a.viirs
    run(modis, viirs, a.out, rows=not a.no_rows)
