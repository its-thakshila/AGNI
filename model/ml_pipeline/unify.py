"""Build the unified multi-year daily record in VIIRS units.

For every MODIS year found in the data folders listed in config.py (config.MODIS_DIRS):
  * real VIIRS S-NPP CSV present (2012+)   -> use the real VIIRS rows / daily counts
  * MODIS only (2000-2011)                  -> use predict.py output (VIIRS-equivalent)
File names and folders: config.MODIS_NAME / config.VIIRS_NAME, config.modis_path() / viirs_path()

Outputs (output/unified/):
  unified_daily.csv          fire_date, viirs_n, viirs_frp_sum, modis_n, source
  unified_viirs_format.csv   all rows in the VIIRS CSV schema (version tells real vs ML)
"""
from __future__ import annotations

import logging

import pandas as pd

import common
import config
import predict

log = logging.getLogger("unify")


def main(write_rows: bool = True):
    config.UNIFIED_DIR.mkdir(parents=True, exist_ok=True)
    years = config.modis_years()
    daily_parts, row_files = [], []
    for y in years:
        mp = config.modis_path(y)
        vp = config.viirs_path(y)
        m = common.load_modis(mp)
        mod_daily = m.groupby("fire_date").size().rename("modis_n")
        if vp is not None:
            v = common.load_viirs(vp)
            g = v.groupby("fire_date")
            d = pd.DataFrame({"viirs_n": g.size(), "viirs_frp_sum": g.frp.sum().round(1)})
            d["source"] = "real_viirs"
            if write_rows:
                row_files.append(vp)
        else:
            daily, _, _ = predict.run(mp, rows=write_rows)
            d = daily.set_index(pd.to_datetime(daily.fire_date))[["viirs_equiv_n_sensitivity_adj", "viirs_equiv_frp_sum"]]
            d.columns = ["viirs_n", "viirs_frp_sum"]
            d["source"] = "ml_predicted"
            if write_rows:
                row_files.append(config.pred_file("viirs_equivalent", mp))
        d = d.join(mod_daily, how="outer").fillna({"viirs_n": 0, "viirs_frp_sum": 0, "modis_n": 0})
        d = d[(d.index >= pd.Timestamp(m.acq_date.min())) & (d.index <= pd.Timestamp(m.acq_date.max()))]
        d["source"] = d["source"].fillna(d["source"].dropna().iloc[0])
        daily_parts.append(d)
        log.info("%d: %s, %d days", y, d.source.iloc[0], len(d))
    u = pd.concat(daily_parts).sort_index()
    u.index = u.index.strftime("%Y-%m-%d")
    u.index.name = "fire_date"
    u.to_csv(config.UNIFIED_DAILY_CSV)
    if write_rows:
        with open(config.UNIFIED_ROWS_CSV, "w", newline="") as fh:
            for i, f in enumerate(row_files):
                pd.read_csv(f, dtype=str)[config.VIIRS_COLUMNS].to_csv(fh, index=False, header=(i == 0))
    log.info("unified record: %d days, years %s -> %s", len(u), years, config.UNIFIED_DIR)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    main()
