"""Training tables for the forecast models: one folder per lead, one Parquet file per issue year.

Row = land cell x pass for target day t + lead, features from issue day t (history.Day.features).
  y      real VIIRS type-0 rows in the cell/pass on the target day (target)
  w      sampling weight: 1 for rows with fire, 1/keep-rate for kept no-fire rows
  split  train / val (by target-day year, fcst_config.TRAIN_YEARS / VAL_YEARS); test is not written,
         evaluate.py scores the test years on the full grid
  frp_mean, ti4_mean, ti5_mean, sh_l, sh_n, sh_h   stage-3 targets (rows with fire only)
Target days with gap-filled or incomplete real data are skipped.

    python build_dataset.py              # leads from fcst_config.LEADS
    python build_dataset.py --weather    # also the NASA POWER weather features (needs data/weather)
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import time

import numpy as np
import pandas as pd

import attributes
import fcst_config as fc
from history import BASE_FEATURES, History, weather_cols

log = logging.getLogger("build")


def split_of(year: int) -> str | None:
    if year in fc.TRAIN_YEARS:
        return "train"
    if year in fc.VAL_YEARS:
        return "val"
    return None


def run(leads=fc.LEADS, use_weather=fc.USE_WEATHER, include_test: bool = False):
    use_weather = "target" if use_weather is True else (use_weather or False)   # False | issue | target
    t0 = time.perf_counter()
    H = History()
    A = attributes.load()
    A["d"] = (A["fire_date"] - H.start).dt.days.astype(np.int32)
    A["night"] = (A["pass"] == "N").astype(np.int8)
    A = A[["d", "ci", "cj", "night", "frp_mean", "ti4_mean", "ti5_mean", "sh_l", "sh_n", "sh_h"]]
    years = list(fc.TRAIN_YEARS) + list(fc.VAL_YEARS) + (list(fc.TEST_YEARS) if include_test else [])
    first = H.day_index(max(pd.Timestamp(fc.TARGET_START), pd.Timestamp(f"{min(years)}-01-01"))) - max(leads)
    last = min(H.day_index(f"{max(years)}-12-31"), H.n_days - 1) - min(leads)
    feats = BASE_FEATURES + (weather_cols() if use_weather else [])

    fc.TABLE_DIR.mkdir(parents=True, exist_ok=True)
    for L in leads:
        d = fc.TABLE_DIR / f"lead_{L}"
        if d.exists():
            shutil.rmtree(d)                    # derived data, rebuilt from the inputs
        d.mkdir(parents=True)
    li, lj = H.land_i, H.land_j
    nl = len(li)
    buf = {L: [] for L in leads}
    stats = {L: {"rows": 0, "pos": 0, "days": 0} for L in leads}
    cur_year = None

    def flush(year):
        for L in leads:
            if not buf[L]:
                continue
            df = pd.concat(buf[L], ignore_index=True)
            buf[L] = []
            pos = df[df.y > 0]
            pos = pos[["tgt_d", "ci", "cj", "night"]].reset_index().merge(
                A, left_on=["tgt_d", "ci", "cj", "night"], right_on=["d", "ci", "cj", "night"], how="left")
            for c in ["frp_mean", "ti4_mean", "ti5_mean", "sh_l", "sh_n", "sh_h"]:
                df[c] = np.float32(np.nan)
                df.loc[pos["index"].to_numpy(), c] = pos[c].to_numpy(np.float32)
            df.to_parquet(fc.TABLE_DIR / f"lead_{L}" / f"issue_{year}.parquet", index=False)
            stats[L]["rows"] += len(df); stats[L]["pos"] += int((df.y > 0).sum())

    for day in H.iterate(first, last):
        yr = day.date.year
        if cur_year is not None and yr != cur_year:
            flush(cur_year)
            log.info("issue year %d done (%.0f s)", cur_year, time.perf_counter() - t0)
        cur_year = yr
        act = day.active()[li, lj]
        rng = np.random.default_rng(fc.SEED + day.d)
        for L in leads:
            td = day.d + L
            if td >= H.n_days or not H.target_ok[td]:
                continue
            sp = split_of(H.date(td).year)
            if sp is None:
                if not (include_test and H.date(td).year in fc.TEST_YEARS):
                    continue
                sp = "test"
            T = H.target(td)
            night = np.repeat(np.array([0, 1], np.int8), nl)
            ii, jj = np.tile(li, 2), np.tile(lj, 2)
            y = T[night, ii, jj]
            rate = np.where(np.tile(act, 2), fc.ACTIVE_KEEP, fc.QUIET_KEEP)
            keep = (y > 0) | (rng.random(2 * nl) < rate)
            ii, jj, night, y, rate = ii[keep], jj[keep], night[keep], y[keep], rate[keep]
            X = day.features(ii, jj, night, L, weather=use_weather)
            X["y"] = y
            X["w"] = np.where(y > 0, 1.0, 1.0 / rate).astype(np.float32)
            X["issue_d"] = np.int32(day.d); X["tgt_d"] = np.int32(td)
            X["ci"] = (ii + H.i0).astype(np.int16); X["cj"] = (jj + H.j0).astype(np.int16)
            X["night"] = night
            X["split"] = sp
            buf[L].append(X)
            stats[L]["days"] += 1
    if cur_year is not None:
        flush(cur_year)

    meta = {"built": pd.Timestamp.now().isoformat(timespec="seconds"), "features": feats, "leads": list(leads),
            "history": [str(H.start.date()), str(H.end.date())], "train_years": list(fc.TRAIN_YEARS),
            "val_years": list(fc.VAL_YEARS), "clim_years": list(H.clim_years),
            "target_source": fc.TARGET_SOURCE, "active_keep": fc.ACTIVE_KEEP, "quiet_keep": fc.QUIET_KEEP, "weather": use_weather, "stats": stats}
    (fc.TABLE_DIR / "meta.json").write_text(json.dumps(meta, indent=1))
    for L in leads:
        log.info("lead %d: %d target days, %d rows (%d with fire)", L, stats[L]["days"], stats[L]["rows"],
                 stats[L]["pos"])
    log.info("done in %.0f s -> %s", time.perf_counter() - t0, fc.TABLE_DIR)
    return meta


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--leads", type=int, nargs="+", default=list(fc.LEADS))
    ap.add_argument("--weather", nargs="?", const="issue", choices=["issue", "target"], default=fc.USE_WEATHER,
                    help="add the NASA POWER weather features of the issue day (default) or the target day")
    ap.add_argument("--with-test", action="store_true", help="also write test-year rows (split = test)")
    a = ap.parse_args()
    run(tuple(a.leads), a.weather, a.with_test)
