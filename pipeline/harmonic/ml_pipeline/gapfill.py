"""Fill VIIRS data gaps (satellite outages) in the unified record with model predictions.

Runs AFTER unify.py and does not modify its outputs. For every year that has a real VIIRS file
(2012+), dates with no VIIRS rows but with MODIS data are predicted from MODIS with the trained
models (same predict.py functions as the MODIS-only years).

Inputs : output/unified/unified_daily.csv, output/unified/unified_viirs_format.csv
Outputs (output/unified/):
  unified_viirs_format_gapfilled.csv  unified rows + model rows for missing VIIRS dates,
                                      date-sorted; gap rows have version = OUT_VERSION + "-gapfill"
  unified_daily_gapfilled.csv         unified_daily with gap days filled, source = "ml_gapfill"
  gapfill_report.csv                  one row per filled date: modis_n, predicted rows

    python gapfill.py                # daily + row files
    python gapfill.py --daily-only   # daily file + report only (fast; no 3 GB row file)
"""
from __future__ import annotations

import io
import logging

import numpy as np
import pandas as pd

import common
import config
import predict

log = logging.getLogger("gapfill")

GAP_VERSION = config.OUT_VERSION + "-gapfill"
CONTEXT_DAYS = 3                    # MODIS days kept around each gap for neighbour features
ROWS_OUT = config.UNIFIED_DIR / "unified_viirs_format_gapfilled.csv"
DAILY_OUT = config.UNIFIED_DIR / "unified_daily_gapfilled.csv"
REPORT_OUT = config.UNIFIED_DIR / "gapfill_report.csv"


def _missing(present: pd.Series, lo: pd.Timestamp, hi: pd.Timestamp) -> pd.DatetimeIndex:
    return pd.date_range(lo, hi).difference(pd.DatetimeIndex(present.unique()))


def predict_gaps(year: int, bundles: dict, stats: dict, rng):
    """Model rows (UTC dates missing in VIIRS) and daily values (fire-days missing in VIIRS)."""
    m = common.load_modis(config.modis_path(year))
    v = common.load_viirs(config.viirs_path(year))
    lo, hi = pd.Timestamp(m.acq_date.min()), pd.Timestamp(m.acq_date.max())
    miss_utc = _missing(pd.to_datetime(v.acq_date), lo, hi)          # row file is keyed by UTC date
    miss_fire = _missing(v.fire_date, lo, hi)                        # daily file is keyed by fire-day
    if len(miss_utc) == 0 and len(miss_fire) == 0:
        return None, None
    need = miss_utc.union(miss_fire)
    near = np.zeros(len(m), bool)
    for d in need:
        near |= (m.fire_date >= d - pd.Timedelta(days=CONTEXT_DAYS)) & (m.fire_date <= d + pd.Timedelta(days=CONTEXT_DAYS))
    ms = m[near].reset_index(drop=True)

    X = predict.predict_cells(ms, bundles)
    for c in ("terra_n", "terra_frp_sum", "aqua_n", "aqua_frp_sum"):      # same NaN guard as predict.run
        X[c] = X[c].fillna(0.0) if c in X else 0.0
    R, X = predict.synthesize_rows(X, ms, stats, rng)
    daily, _ = predict.daily_tables(X, stats, None)

    R = R[pd.to_datetime(R.acq_date).isin(miss_utc)].copy()
    R["version"] = GAP_VERSION
    daily = daily[pd.to_datetime(daily.fire_date).isin(miss_fire)]
    log.info("%d: %d UTC dates / %d fire-days missing in VIIRS -> %d model rows",
             year, len(miss_utc), len(miss_fire), len(R))
    return R, daily


def merge_rows(gap: pd.DataFrame):
    """Stream the unified row file and insert gap rows in date order (file is date-sorted)."""
    buf = io.StringIO()                       # round-trip through CSV text: same formatting as unify.py
    gap.sort_values(["acq_date", "acq_time"], kind="mergesort").to_csv(buf, index=False)
    buf.seek(0)
    gap = pd.read_csv(buf, dtype=str, keep_default_na=False)[config.VIIRS_COLUMNS]
    pos, n_in, prev = 0, 0, ""
    with open(ROWS_OUT, "w", newline="") as fh:
        for i, ch in enumerate(pd.read_csv(config.UNIFIED_ROWS_CSV, dtype=str, keep_default_na=False,
                                           chunksize=2_000_000)):
            if ch.acq_date.iloc[0] < prev or not ch.acq_date.is_monotonic_increasing:
                raise RuntimeError("unified_viirs_format.csv is not date-sorted; cannot merge in order")
            prev = ch.acq_date.iloc[-1]
            end = int(np.searchsorted(gap.acq_date.to_numpy(), prev, side="right"))
            ins = gap.iloc[pos:end]
            pos = end
            if len(ins):
                ch = pd.concat([ch, ins]).sort_values("acq_date", kind="mergesort")
            ch.to_csv(fh, index=False, header=(i == 0))
            n_in += len(ch)
        if pos < len(gap):                    # gaps after the last real date
            gap.iloc[pos:].to_csv(fh, index=False, header=False)
            n_in += len(gap) - pos
    return n_in


def main(write_rows: bool = True):
    bundles, stats = predict.load_models()
    rng = np.random.default_rng(config.RANDOM_SEED)
    rows, dailies = [], []
    for y in config.modis_years():
        if config.viirs_path(y) is None:
            continue
        R, d = predict_gaps(y, bundles, stats, rng)
        if R is not None:
            rows.append(R)
            dailies.append(d)
    if not rows:
        log.info("no VIIRS gaps found - nothing to do")
        return
    gap_rows = pd.concat(rows, ignore_index=True)
    gap_daily = pd.concat(dailies, ignore_index=True).set_index("fire_date")

    # ---- daily record
    u = pd.read_csv(config.UNIFIED_DAILY_CSV, index_col="fire_date")
    fill = u.index.intersection(gap_daily.index)
    u.loc[fill, "viirs_n"] = gap_daily.loc[fill, "viirs_equiv_n_sensitivity_adj"]
    u.loc[fill, "viirs_frp_sum"] = gap_daily.loc[fill, "viirs_equiv_frp_sum"]
    u.loc[fill, "source"] = "ml_gapfill"
    u.to_csv(DAILY_OUT)

    rep = pd.DataFrame({"fire_date": fill, "modis_n": u.loc[fill, "modis_n"].to_numpy(),
                        "viirs_n_predicted": u.loc[fill, "viirs_n"].to_numpy()})
    rep["rows_generated_utc_date"] = rep.fire_date.map(gap_rows.groupby("acq_date").size()).fillna(0).astype(int)
    rep.to_csv(REPORT_OUT, index=False)
    log.info("daily: %d gap days filled -> %s", len(fill), DAILY_OUT)

    # ---- row record
    if not write_rows:
        log.info("daily only: row file not written")
        return
    n = merge_rows(gap_rows)
    log.info("rows: %d gap rows inserted, %d rows total -> %s", len(gap_rows), n, ROWS_OUT)


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    main(write_rows="--daily-only" not in sys.argv[1:])
